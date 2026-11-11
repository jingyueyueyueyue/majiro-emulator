"""Find public source controls without inventing menu or progress state."""
from test_desktop_game_batch import click, settle, tick


def visible(session, area):
    width, height = session.lua.globals()["PLAYER_SIZE"]()
    args = area["args"]
    return (args[4] > 0 and args[5] > 0
            and 0 <= args[2] + args[4] / 2 < width
            and 0 <= args[3] + args[5] / 2 < height)


def story_menu_control(session, target):
    assert target in ("history.mjo", "loadsave.mjo")
    state = session.system["state"]
    identifier = 6 if target == "history.mjo" else 0
    area = next((a for a in state["ui_areas"].values()
                 if a["args"][1] == identifier and a["script"] == "console.mjo"
                 and visible(session, a)), None)
    if area is not None:
        return area, ["console.mjo", identifier]
    # Some original UIs expose their controls through a right-click popup.
    tick(session, {"buttons": 2, "held_buttons": 2}, 500)
    tick(session, {"buttons": 0, "held_buttons": 0}, 50)
    settle(session, 1000, 4000)
    if state["active_ui_script"] == "menu.mjo":
        identifier = 2 if target == "history.mjo" else 0
        area = next((a for a in state["ui_areas"].values()
                     if a["args"][1] == identifier and visible(session, a)), None)
        if area is not None:
            return area, ["right", "menu.mjo", identifier]
    return None, ["source_boundary", state["active_ui_script"]]


def open_story_history(session):
    """Use visible source controls or the real source PageUp callback."""
    state = session.system["state"]
    direct = next((a for a in state["ui_areas"].values()
                   if a["args"][1] == 6 and a["script"] == "console.mjo"
                   and visible(session, a)), None)
    if direct is not None:
        click(session, direct["args"])
        return ["console.mjo", 6]
    tick(session, {"pressed": session.lua.table_from({33: True}),
                   "buttons": 0, "held_buttons": 0}, 50)
    tick(session, {"buttons": 0, "held_buttons": 0}, 50)
    settle(session, 1000, 4000)
    if state["active_ui_script"] == "history.mjo":
        return ["PageUp", 33, "history.mjo"]
    area, path = story_menu_control(session, "history.mjo")
    if area is not None:
        click(session, area["args"])
        return path
    return path


def open_story_save(session):
    """Open the original save UI via its control or native window-menu event."""
    state = session.system["state"]
    before=len(session.host_events)
    direct = next((a for a in state["ui_areas"].values()
                   if a["args"][1] == 0 and a["script"] == "console.mjo"
                   and visible(session, a)), None)
    if direct is not None:
        click(session, direct["args"])
        if state["active_ui_script"] == "loadsave.mjo":
            return ["console.mjo", 0]
        if any(e.get("kind")=="native_dialog_cancel_fixture"for e in session.host_events[before:]):
            return ["console.mjo",0,"native_dialog_fixture"]
    tick(session, {"window_command": 3, "buttons": 0, "held_buttons": 0}, 50)
    settle(session, 1000, 4000)
    if state["active_ui_script"] == "loadsave.mjo":
        return ["window", 3, "SAVEMENU"]
    if any(e.get("kind")=="native_dialog_cancel_fixture"for e in session.host_events[before:]):
        return ["window",3,"native_dialog_fixture"]
    area, path = story_menu_control(session, "loadsave.mjo")
    if area is not None:
        click(session, area["args"])
        return path
    return path + [state["window_menu_result"] or "unavailable"]
