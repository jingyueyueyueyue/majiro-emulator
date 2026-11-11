"""Normal original-script navigation with public input, never scene-PC rewrites."""
from __future__ import annotations
from lua_bridge import create_lua, setup_runtime, table_value


class Session:
    def __init__(self, allow_prepare=True,save_root=None):
        self.lua=create_lua()
        _,self.vm,self.system,self.clock=setup_runtime(self.lua,allow_prepare,save_root=save_root)
        self.vm["begin"](self.vm,"start.mjo")

    def event(self, **values):
        event=self.lua.table_from(values,recursive=True)
        self.system["poll"](self.vm,event)

    def advance(self, maximum=4_000_000, stop_at_text=True, previous_generation=None):
        if self.vm["status"]=="blocked":
            return "blocked"
        initial=self.vm["steps"]
        for _ in range(20000):
            try:
                status=self.vm["run"](self.vm,10000)
                if status=="blocked":
                    return status
                if self.vm["steps"]-initial>maximum:
                    raise RuntimeError("original-script campaign instruction budget")
                pending=self.system["state"]["input"]
                if (status=="waiting" and self.system["state"]["ui_waiting"] and pending["buttons"]==0
                    and not self.system["state"]["reading_wait"]):
                    return "ui"
                if (status=="waiting" and self.system["state"]["reading_wait"] and pending["buttons"]==0
                    and stop_at_text and (previous_generation is None or
                        self.system["state"]["reading_generation"]!=previous_generation)):
                    return "text"
                if status=="finished":
                    return status
                if status=="waiting":
                    main_wait=self.vm["threads"][1]["waiting"]
                    if main_wait and main_wait["kind"]=="input":
                        return "input"
                    deadline=self.vm["next_deadline"](self.vm)
                    if deadline is None:
                        return "input"
                    self.clock["ms"]=max(self.clock["ms"]+10,deadline)
                    self.event()
            except Exception:
                if self.vm["status"]=="blocked":
                    return "blocked"
                raise
        raise RuntimeError("original-script campaign frame budget")

    def click(self,index):
        areas=self.system["state"]["ui_areas"]
        area=areas[index]["args"]
        x,y=area[2]+area[4]//2,area[3]+area[5]//2
        self.system["state"]["ui_waiting"]=False
        self.event(x=x,y=y)
        status=self.advance()
        if status=="blocked":
            return status
        self.system["state"]["ui_waiting"]=False
        self.event(x=x,y=y,buttons=1)
        return self.advance()

    def page(self):
        previous=self.system["state"]["reading_generation"]
        # Respect the source's 350ms click debounce instead of sending
        # artificially instantaneous clicks on short consecutive lines.
        self.clock["ms"]+=400
        self.event()
        self.vm["run"](self.vm,10000)
        self.system["state"]["reading_wait"]=False
        self.event(x=1000,y=650,buttons=1)
        return self.advance(previous_generation=previous)

    def summary(self):
        state=self.system["state"]
        return {
            "status":self.vm["status"],"steps":self.vm["steps"],"simulated_ms":self.clock["ms"],
            "problem":table_value(self.vm["problem"])if self.vm["problem"]else None,
            "last_syscall":table_value(self.vm["last_syscall"])if self.vm["last_syscall"]else None,
            "areas":table_value(state["ui_areas"])if state["ui_areas"]else None,
            "loaded_scripts":sorted(self.vm["scripts"].keys()),
            "console":table_value(state["console"]),
        }
