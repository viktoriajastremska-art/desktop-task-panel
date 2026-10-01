#!/usr/bin/env python3
"""Backend for the desktop task panel.

Responsibilities:
  * persist tasks to ~/.cache/task-panel/tasks.json
  * turn a raw task line into an English title + description via any
    OpenAI-compatible chat-completions endpoint
  * create the issue in Jira and read back its status

Everything here is plain stdlib: urllib + json. No external deps.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, ".config", "task-panel", "config.json")
CACHE_DIR = os.path.join(HOME, ".cache", "task-panel")
TASKS_FILE = os.path.join(CACHE_DIR, "tasks.json")

_open_cache = {"cfg": None}


def config():
    if _open_cache["cfg"] is None:
        with open(CONFIG) as f:
            _open_cache["cfg"] = json.load(f)
    return _open_cache["cfg"]


def llm_api_key(cfg):
    """Resolve the LLM key: explicit config, env var, or a local auth file."""
    if cfg.get("api_key"):
        return cfg["api_key"]
    env = cfg.get("api_key_env") or "LLM_API_KEY"
    if os.environ.get(env):
        return os.environ[env]
    path = os.path.expanduser(cfg.get("auth_path", "") or "")
    account = cfg.get("auth_account")
    if path and account:
        try:
            with open(path) as f:
                return (json.load(f).get(account) or {}).get("key")
        except Exception:
            return None
    return None


# ---------------------------------------------------------------- tasks store
def new_id():
    return uuid.uuid4().hex[:12]


def default_store():
    return {"tasks": [], "updated_at": int(time.time())}


def load_tasks():
    try:
        with open(TASKS_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            return data
    except Exception:
        pass
    return default_store()


def save_tasks(store):
    os.makedirs(CACHE_DIR, exist_ok=True)
    store["updated_at"] = int(time.time())
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TASKS_FILE)


# --------------------------------------------------------------------- LLM
_SYS = (
    "You convert a short, messy task note into a Jira ticket for an "
    "engineering team. Reply with ONLY compact JSON, no markdown fences, "
    "no prose. Schema: {\"title\": string, \"description\": string}. "
    "title: concise English imperative, max 80 chars. "
    "description: 2-4 sentences of clear English context and expected outcome."
)


def _post_json(url, payload, headers, timeout=90):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _extract_json(text):
    """Pull the first {...} object out of a possibly chatty answer."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def generate_ticket(task_text):
    """Return (title, description). Raises on failure."""
    cfg = config()["llm"]
    key = llm_api_key(cfg)
    if not key:
        raise RuntimeError("no LLM API key configured")
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _SYS},
            {"role": "user", "content": "Task note: " + task_text.strip()},
        ],
        "max_tokens": cfg.get("max_tokens", 4000),
        "temperature": cfg.get("temperature", 0.2),
    }
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": cfg.get("user_agent", "task-panel/1.0"),
    }
    headers.update(cfg.get("headers") or {})
    d = _post_json(cfg["url"], payload, headers, timeout=120)
    msg = (d.get("choices") or [{}])[0].get("message", {})
    parsed = _extract_json(msg.get("content"))
    if not parsed:
        raise RuntimeError("LLM returned an invalid response")
    title = (parsed.get("title") or task_text).strip()
    desc = (parsed.get("description") or "").strip()
    if len(title) > 250:
        title = title[:247] + "..."
    return title, desc


# --------------------------------------------------------------------- Jira
def _jira(method, path, payload=None, timeout=30):
    cfg = config()["jira"]
    url = cfg["base_url"].rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + cfg["token"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        return json.loads(body) if body else {}


def jira_create_issue(title, description):
    """Create a Task in the configured project, assigned to the user."""
    cfg = config()["jira"]
    fields = {
        "project": {"key": cfg["project"]},
        "summary": title,
        "description": description,
        "issuetype": {"id": cfg["issue_type_id"]},
    }
    if cfg.get("assignee"):
        fields["assignee"] = {"name": cfg["assignee"]}
    payload = {"fields": fields}
    try:
        return _jira("POST", "/rest/api/2/issue", payload, timeout=40)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Jira {e.code}: {detail[:300]}")


def jira_issue(key):
    return _jira("GET", f"/rest/api/2/issue/{key}?fields=status,summary,assignee")


def jira_status(key):
    try:
        d = jira_issue(key)
        return (d.get("fields", {}).get("status") or {}).get("name") or "?"
    except Exception:
        return None


def jira_transitions(key):
    """Available workflow transitions for an issue."""
    return _jira("GET", f"/rest/api/2/issue/{key}/transitions").get("transitions", [])


def _find_done_transition(transitions):
    """Pick the transition whose name (or target status) contains 'done'."""
    for t in transitions:
        if "done" in (t.get("name") or "").lower():
            return t
    for t in transitions:
        target = (t.get("to") or {}).get("name") or ""
        if "done" in target.lower():
            return t
    return None


def jira_transition_done(key):
    """Move an issue through its Done transition. Returns the new status name."""
    tr = _find_done_transition(jira_transitions(key))
    if not tr:
        raise RuntimeError(f"no Done transition available for {key}")
    try:
        _jira("POST", f"/rest/api/2/issue/{key}/transitions",
              {"transition": {"id": tr["id"]}}, timeout=30)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Jira {e.code}: {detail[:300]}")
    return jira_status(key)


def jira_my_open_count(project=None):
    """How many open issues are assigned to me (optionally in one project)."""
    cfg = config()["jira"]
    project = project or cfg["project"]
    jql = (f'project = {project} AND assignee = currentUser() '
           f'AND resolution = Unresolved ORDER BY updated DESC')
    path = "/rest/api/2/search?maxResults=0&jql=" + urllib.parse.quote(jql)
    try:
        d = _jira("GET", path)
        return int(d.get("total", 0))
    except Exception:
        return None
