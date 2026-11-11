"""Behavior tests with independently authored programs and restart boundaries."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest

from lua_bridge import create_lua, table_value
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, write_json

HARNESS = r'''
local mod,opcodes = VM_MODULE,OPCODES
function makevm(code,functions,main,syscall)
  local script={name='fixture.mjo',code=code,main=main or 0,functions=functions or {}}
  local vm=mod.new{opcodes=opcodes,loader=function()return script end,
    syscall=syscall or function(v) v:fail('fixture syscall missing') end}
  vm:begin('fixture.mjo')
  return vm
end
function finish(vm)
  local ok,why=pcall(function() for i=1,100 do
    if vm:run(100)=='finished' then return end
  end;error('fixture budget')end)
  return ok,why
end
'''


class VMTests(unittest.TestCase):
    def setUp(self):
        self.lua = create_lua()
        self.lua.globals()["VM_MODULE"] = self.lua.execute((ROOT / "runtime/majiro_vm.lua").read_text(encoding="utf-8"))
        self.lua.globals()["OPCODES"] = self.lua.execute((ROOT / "resources/scripts/opcodes.lua").read_text(encoding="utf-8"))
        self.lua.execute(HARNESS)

    def test_integer_width_and_negative_remainder(self):
        got = self.lua.execute("""
          local v=makevm{{0,0x800,2147483647},{6,0x800,1},{12,0x118},
            {14,0x1b0,32,123,-1},{24,0x82f},
            {26,0x800,-7},{32,0x800,3},{38,0x110},
            {40,0x1b0,32,124,-1},{50,0x82b}}
          assert(finish(v));return {v.globals[1][123],v.globals[1][124]}
        """)
        self.assertEqual(table_value(got), [-2147483648, -1])

    def test_full_width_multiply_and_shift(self):
        got = self.lua.execute("""
          local v=makevm{{0,0x800,0},{6,0x82b}}
          return {v:binary('mul',-1,-1,'i'),v:binary('shl',2147483647,31,'i'),
            v:binary('shr',-8,2,'i'),v:binary('and',-1,255,'i')}
        """)
        self.assertEqual(table_value(got), [1, -2147483648, -2, 255])

    def test_argument_order_and_return_frame(self):
        got = self.lua.execute("""
          local v=makevm({
            {0,0x800,7},{6,0x800,11},{12,0x80f,42,0,2},
            {24,0x1b0,32,100,-1},{34,0x82b},
            {36,0x836,{0,0}},{42,0x829,{}},
            {46,0x802,96,1,-2},{56,0x802,96,2,-3},{66,0x120},{68,0x82b}},
            {{42,36}})
          assert(finish(v));return {v.globals[1][100],#v.frames,#v.stack}
        """)
        self.assertEqual(table_value(got), [4, 0, 0])

    def test_callback_parameters_are_values_and_array_contents_are_shared(self):
        got = self.lua.execute("""
          local v=makevm{{0,0x800,0},{6,0x82b}}
          local array={kind='array',dims={1},element_type=0,values={}}
          local args={200,array}
          local script=v:frame().script
          v:enter(script,0,args,false)
          v:frame().args[1]=66
          v:frame().args[2].values[0]=99
          v:enter(script,0,args,false)
          return {args[1],v:frame().args[1],v:frame().args[2].values[0]}
        """)
        self.assertEqual(table_value(got), [200, 200, 99])

    def test_postincrement_and_array_write(self):
        got = self.lua.execute("""
          local v=makevm{{0,0x800,0},{6,0x82b}}
          v.globals[1][7]={kind='array',dims={3},element_type=0,values={}}
          local code={{0,0x800,99},{6,0x800,2},{12,0x270,2080,7,-1},
            {22,0x82f},{24,0x800,2},{30,0x837,2080,7,-1},{40,0x82b}}
          v.frames[1].script.code=code;v.frames[1].script.positions={}
          for i,r in ipairs(code)do v.frames[1].script.positions[r[1]]=i end
          assert(finish(v));return v.globals[1][7].values[2]
        """)
        self.assertEqual(got, 99)

    def test_fail_closed_and_instruction_budget(self):
        got = self.lua.execute("""
          local v=makevm{{0,0x82c,0}}
          assert(v:run(23)=='running' and v.steps==23)
          local b=makevm{{0,0x835,0xdeadbeef,0},{8,0x800,0},{14,0x82b}}
          local ok=finish(b)
          return {ok,b.status,b.problem.offset,b.steps}
        """)
        self.assertEqual(table_value(got), [False, "blocked", 0, 1])

    def test_binary32_rounding(self):
        got = self.lua.execute("""
          return {VM_MODULE.f32(16777217),VM_MODULE.f32(16777219),
            VM_MODULE.f32(1+2^-24)}
        """)
        self.assertEqual(table_value(got), [16777216, 16777220, 1])

    def test_threads_progress_while_main_waits(self):
        got=self.lua.execute("""
          local v=makevm({{0,0x82c,0},{6,0x800,91},{12,0x1b0,32,77,-1},{22,0x82b}},
            {{42,6}})
          v:spawn(42,{},false)
          assert(v:run(800)=='running')
          return {v.globals[1][77],v.threads[2].finished,v.thread_id}
        """)
        self.assertEqual(table_value(got),[91,True,1])

    def test_destructor_runs_on_thread_cancel(self):
        got=self.lua.execute("""
          local v=makevm({{0,0x82c,0},
            {6,0x847,26},{12,0x82c,12},
            {26,0x844},{28,0x800,11},{34,0x1b0,32,99,-1},{44,0x82b}},
            {{42,6}})
          v:spawn(42,{},false);v:run(600);v:terminate(2);v:run(600)
          return {v.globals[1][99],v.threads[2].finished}
        """)
        self.assertEqual(table_value(got),[11,True])

    def test_context_exit_cleans_up_then_reenters_main(self):
        got=self.lua.execute("""
          local v=makevm{{0,0x847,26},{6,0x82c,6},
            {26,0x844},{28,0x800,11},{34,0x1b0,0,99,-1},{44,0x82b}}
          v.globals[1][88]=73
          v:run(600);v:exit_context();v:run(600)
          return {v.globals[0][99],v.status,v.exiting_context==nil,
            #v.frames,v.threads[1].terminate_requested==nil,v.globals[1][88]==nil}
        """)
        self.assertEqual(table_value(got),[11,"running",True,1,True,True])

    def test_function_only_script_registers_functions_without_inventing_main(self):
        got=self.lua.execute("""
          local library={name='functions.mjo',main=4294967295,functions={{42,0}},
            code={{0,0x800,91},{6,0x82b}}}
          local entry={name='start.mjo',main=0,functions={},
            code={{0,0x80f,42,0,0},{12,0x1b0,32,123,-1},{22,0x82b}}}
          local v=VM_MODULE.new{opcodes=OPCODES,
            syscall=function()error('unexpected syscall')end,
            loader=function(name)return name=='functions.mjo'and library or entry end}
          v:load('functions')
          assert(library.main==4294967295)
          v:begin('start')
          assert(finish(v))
          return {v.globals[1][123],library.main}
        """)
        self.assertEqual(table_value(got),[91,4294967295])

    def test_cross_frame_input_runs_nested_destructor_helpers(self):
        got=self.lua.execute("""
          local v=makevm({
            {0,0x83b,100},{6,0x810,42,0,0},{18,0x82c,18},
            {30,0x847,60},{36,0x82c,36},
            {60,0x844},{62,0x810,99,0,0},{74,0x800,0},{80,0x82b},
            {100,0x844},{102,0x800,77},{108,0x1b0,32,100,-1},{116,0x82f},{118,0x800,0},{124,0x82b},
            {140,0x800,88},{146,0x1b0,32,101,-1},{154,0x82f},{156,0x800,0},{162,0x82b}},
            {{42,30},{99,140}})
          v:run(200)
          assert(v:signal('left'))
          assert(finish(v))
          return {v.globals[1][100],v.globals[1][101],#v.stack}
        """)
        self.assertEqual(table_value(got),[77,88,0])

    def test_host_deadline_preserves_suspended_program(self):
        got=self.lua.execute("""
          local v=makevm{{0,0x82c,0}}
          local ticks=0
          local function clock()ticks=ticks+1;return ticks end
          assert(v:run(10000,2,clock)=='running' and v.steps>0 and v.steps<256)
          local prior=v.steps
          v:run(10000,ticks+2,clock)
          return {v.steps>prior and v.steps<prior+256,
            v:frame().script.code[v:frame().index][1]}
        """)
        self.assertEqual(table_value(got),[True,0])

    def test_real_argument_and_return_keep_native_bits_for_array_assignment(self):
        got=self.lua.execute("""
          local v=makevm({
            {0,0x803,1},{6,0x80f,42,0,1},{18,0x800,0},
            {24,0x270,32+2048,7,-1},{34,0x82b},
            {40,0x836,{1}},{44,0x829,{}},{48,0x802,96+256,1,-2},{58,0x82b}},
            {{42,40}})
          v.globals[1][7]={kind='array',dims={1},element_type=0,values={}}
          assert(finish(v))
          return v.globals[1][7].values[0]
        """)
        self.assertEqual(got,1065353216)

    def test_checkpoint_keeps_real_stack_type_at_an_integer_valued_float(self):
        self.lua.globals()["CODEC"] = self.lua.execute(
            (ROOT/"runtime/majiro_codec.lua").read_text(encoding="utf-8"))
        got=self.lua.execute("""
          local code={{0,0x803,1},{6,0x800,0},{12,0x270,32+2048,7,-1},{22,0x82b}}
          local v=makevm(code)
          v.globals[1][7]={kind='array',dims={1},element_type=0,values={}}
          v:run(1)
          local saved=CODEC.decode(CODEC.encode(v:export_state()))
          local restored=makevm(code)
          restored:import_state(saved)
          assert(finish(restored))
          return {restored.globals[1][7].values[0],#restored.stack}
        """)
        self.assertEqual(table_value(got),[1065353216,0])

    def test_thread_switch_keeps_independent_numeric_type_stacks(self):
        got=self.lua.execute("""
          local v=makevm({{0,0x82c,0},
            {6,0x800,0},{12,0x82b}},{{42,6}})
          v:push(1,1)
          local worker=v:spawn(42,{},false)
          v:select_thread(worker)
          v:push(9,0)
          v:select_thread(1)
          local first,first_type=v:pop_typed()
          v:select_thread(worker)
          local second,second_type=v:pop_typed()
          return {first,first_type,second,second_type}
        """)
        self.assertEqual(table_value(got),[1,1,9,0])

    def test_source_thread_call_keeps_integral_float_parameter(self):
        from audit_player_semantics import registered_providers
        lua, _, _, system = registered_providers()
        lua.globals()["MOD"] = lua.execute((ROOT/"runtime/majiro_vm.lua").read_text(encoding="utf-8"))
        lua.globals()["OPS"] = lua.execute((ROOT/"resources/scripts/opcodes.lua").read_text(encoding="utf-8"))
        lua.globals()["INVOKE"] = system["invoke"]
        got=lua.execute("""
          local script={name='thread-types',main=0,functions={{42,100}},
            code={{0,0x803,1},{6,0x800,42},{12,0x834,0xf2f9daa8,2},
              {20,0x82f},{22,0x82c,22},
              {100,0x836,{1}},{104,0x829,{}},{108,0x802,96+256,1,-2},
              {118,0x800,0},{124,0x270,32+2048,7,-1},{134,0x82b}}}
          local vm=MOD.new{loader=function()return script end,opcodes=OPS,syscall=INVOKE}
          vm:begin(script.name)
          vm.globals[1][7]={kind='array',dims={1},element_type=0,values={}}
          vm:run(800)
          return {vm.globals[1][7].values[0],vm.threads[2].finished}
        """)
        self.assertEqual(table_value(got),[1065353216,True])

    def test_case_selector_is_shared_by_callees_and_retains_comparison_operand(self):
        got=self.lua.execute("""
          local v=makevm({
            {0,0x800,7},{6,0x830,12},{12,0x810,42,0,0},
            {24,0x800,5},{30,0x831,80},{36,0x800,1},{42,0x1b0,32,100,-1},{52,0x82b},
            {80,0x800,2},{86,0x1b0,32,100,-1},{96,0x82b},
            {100,0x800,3},{106,0x830,112},{112,0x800,0},{118,0x82b}},
            {{42,100}})
          v:run(7)
          assert(#v.stack==0 and v.globals[2][0x11f91fd3]==3)
          assert(finish(v))
          return {v.globals[1][100],#v.stack}
        """)
        self.assertEqual(table_value(got),[2,0])

    def test_return_cleans_case_temporaries_and_preserves_top_result(self):
        got=self.lua.execute("""
          local v=makevm({
            {0,0x80f,42,0,0},{12,0x1b1,32+256,100,-1},{22,0x82b},
            {100,0x800,91},{106,0x800,92},{112,0x803,1.25},{118,0x82b}},
            {{42,100}})
          assert(finish(v))
          return {v.globals[1][100],#v.stack}
        """)
        self.assertEqual(table_value(got),[1.25,0])


def run():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(VMTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"status": "passed" if result.wasSuccessful() else "failed",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "validation": "independent Lua fixtures; original EXE semantic comparison pending"}
    write_json(ROOT / "reports/vm_tests.json", report)
    if not result.wasSuccessful():
        raise RuntimeError("VM regression failed")
    return report


if __name__ == "__main__":
    run()
