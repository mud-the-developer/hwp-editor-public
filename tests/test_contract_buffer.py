"""Same adversarial synthetic buffer checks for the new non-CLI primitive module."""

import importlib
from unittest.mock import patch
import unittest
import test_native_buffer as suite

buffer = importlib.import_module("hwp_native_buffer")


class ContractBufferTests(suite.NativeBufferTests):
    def setUp(self):
        replacement = patch.object(suite, "native_hwp", buffer)
        replacement.start()
        self.addCleanup(replacement.stop)


if __name__ == "__main__":
    unittest.main()
