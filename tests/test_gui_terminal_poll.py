"""Actual harness callback against a fake browser state; NO Tk/SDK/COM/Native."""

from pathlib import Path
import re
import shutil
import subprocess
import unittest


class FillTerminalPollTests(unittest.TestCase):
    def callback_check(self, checks):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node unavailable; never installed automatically")
        source = (Path(__file__).with_name("test_gui_workflow.mjs")).read_text(
            encoding="utf-8"
        )
        callbacks = re.findall(
            r"await until\((async\(\)=>\{[^\n]+?\}),\s*'one actual fill job; read-only reload never resends'\);",
            source,
        )
        self.assertEqual(
            len(callbacks),
            1,
            "Check the exact fill completion callback, not a copied implementation",
        )
        script = (
            """
import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
let state, reloads;
const client={evaluate:async expression=>{
 if(expression==='!busy')return !state.busy;
 assert(expression.includes('state.latest_attempt'),'Unexpected browser expression');
 return !state.busy&&!state.active&&!!state.attempt&&state.attempt.status!=='in_progress';
}};
const click=async id=>{assert.equal(id,'reload');reloads++;state.busy=true};
// Trusted repository callback only; this is a functional test, NOT an OS sandbox.
const callback=runInNewContext('('+process.argv[1]+')',{client,click});
"""
            + checks
        )
        result = subprocess.run(
            [node, "--input-type=module", "-e", script, callbacks[0]],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def test_passed_and_blocked_are_seen_before_any_new_reload(self):
        self.callback_check("""
for(const status of ['checks_passed_needs_human_visual_review','blocked']){
 state={busy:false,active:false,attempt:{status}};reloads=0;
 assert.equal(await callback(),true,'Terminal result must be returned before reload');
 assert.equal(reloads,0,'Terminal result must not start another asynchronous load');
}
""")

    def test_in_progress_reloads_only_when_idle_and_does_not_starve_terminal_state(
        self,
    ):
        self.callback_check("""
state={busy:false,active:true,attempt:{status:'in_progress'}};reloads=0;
assert.equal(await callback(),false);assert.equal(reloads,1);
assert.equal(await callback(),false);assert.equal(reloads,1);
state={busy:false,active:false,attempt:{status:'blocked'}};
assert.equal(await callback(),true);assert.equal(reloads,1);
""")


if __name__ == "__main__":
    unittest.main()
