import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


PROJECT_DIR = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "run_pipeline", PROJECT_DIR / "scripts" / "run_pipeline.py"
)
run_pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(run_pipeline)


class RunPipelineTests(unittest.TestCase):
    def test_requires_hf_api_key_before_running_any_stage(self):
        runner = Mock()

        result = run_pipeline.run_pipeline(environment={}, runner=runner)

        self.assertEqual(result, 1)
        runner.assert_not_called()

    def test_runs_every_stage_in_order_with_the_current_environment(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory)
            result = run_pipeline.run_pipeline(
                environment={"HF_DATA_API_KEY": "test-key"},
                runner=runner,
                python="test-python",
                project_dir=project_dir,
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            [Path(command[1]).name for command, _ in calls],
            [script for _, script in run_pipeline.PIPELINE_STEPS],
        )
        self.assertTrue(all(command[0] == "test-python" for command, _ in calls))
        self.assertTrue(all(kwargs["check"] is False for _, kwargs in calls))
        self.assertTrue(all(kwargs["env"]["HF_DATA_API_KEY"] == "test-key" for _, kwargs in calls))

    def test_stops_at_the_first_failed_stage(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(Path(command[1]).name)
            return subprocess.CompletedProcess(
                command,
                7 if calls[-1] == "update_prices.py" else 0,
            )

        result = run_pipeline.run_pipeline(
            environment={"HF_DATA_API_KEY": "test-key"}, runner=runner
        )

        self.assertEqual(result, 7)
        self.assertEqual(
            calls,
            ["update_universe.py", "update_snapshot.py", "update_prices.py"],
        )


if __name__ == "__main__":
    unittest.main()
