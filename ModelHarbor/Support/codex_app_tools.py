"""Merge deferred Codex app tools into routed requests.

The Codex client registers app tools with deferred loading and executes them
natively; routed providers never see the deferred definitions, so the model
cannot call them. This module merges a versioned snapshot into the request:
client-supplied definitions always win; the snapshot fills in only the tools
the client deferred. Nothing here executes a tool.
"""
import copy
import json
import pathlib

SNAPSHOT_PATH = pathlib.Path(__file__).with_name('codex_app_tools.json')


def load_snapshot(path=SNAPSHOT_PATH):
    """Load and validate the snapshot. Any defect means no merge."""
    try:
        document = json.loads(path.read_bytes())
        tools = document['tools']
        if not isinstance(tools, list):
            return None
        for entry in tools:
            if not isinstance(entry, dict) or entry.get('type') != 'namespace':
                return None
            if not isinstance(entry.get('name'), str):
                return None
            children = entry.get('tools')
            if not isinstance(children, list) or any(
                    not isinstance(child, dict) or child.get('type') != 'function'
                    or not isinstance(child.get('name'), str) for child in children):
                return None
        return document
    except (OSError, ValueError, KeyError):
        return None


def merge(tools, snapshot):
    """Fill in snapshot tools the client deferred. Client definitions win.
    Returns (merged_tools, changed)."""
    if not isinstance(tools, list) or not isinstance(snapshot, dict):
        return tools, False
    snapshot_tools = snapshot.get('tools')
    if not isinstance(snapshot_tools, list):
        return tools, False
    snapshot_namespaces = {}
    for entry in snapshot_tools:
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
            return tools, False
        children = entry.get('tools')
        if not isinstance(children, list) or any(
                not isinstance(child, dict) or not isinstance(child.get('name'), str)
                for child in children):
            return tools, False
        snapshot_namespaces[entry['name']] = {child['name']: child for child in children}
    merged = []
    changed = False
    seen = set()
    for tool in tools:
        if isinstance(tool, dict) and tool.get('type') == 'namespace' and tool.get('name') in snapshot_namespaces:
            full = snapshot_namespaces[tool['name']]
            present = {child.get('name') for child in tool.get('tools', []) if isinstance(child, dict)}
            missing = [child for name, child in full.items() if name not in present]
            if missing:
                tool = copy.deepcopy(tool)
                tool['tools'] = list(tool.get('tools', [])) + copy.deepcopy(missing)
                changed = True
            seen.add(tool['name'])
        merged.append(tool)
    for name, children in snapshot_namespaces.items():
        if name in seen:
            continue
        merged.append({'type': 'namespace', 'name': name,
                       'description': 'Tools provided by the Codex app.',
                       'tools': copy.deepcopy(list(children.values()))})
        changed = True
    return merged, changed


def staleness(snapshot, current_version):
    """Return a warning string when the snapshot was captured under a
    different client version, else None."""
    if not isinstance(snapshot, dict) or not current_version:
        return None
    captured = snapshot.get('captured_with')
    if captured and captured != current_version:
        return 'codex app tool snapshot captured with %s; running %s' % (captured, current_version)
    return None
