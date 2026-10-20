"""sdk/ESPidiPad: the SMC-PAD Pro kit's own checks, and PadCore.h on this computer.

The C++ parts need a host compiler (c++); without one they are skipped."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PAD = os.path.join(ROOT, "sdk", "ESPidiPad")
KIT = os.path.join(PAD, "extras", "smc_pad_ble")
TEST = os.path.join(PAD, "extras", "test")
CXX = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


class KitTest(unittest.TestCase):
    def test_protocol_header_is_the_kits(self):
        with open(os.path.join(PAD, "src", "SmcPadProtocol.h"), "rb") as a, \
                open(os.path.join(KIT, "esp32", "SmcPadProtocol.h"), "rb") as b:
            self.assertEqual(a.read(), b.read(), "src/SmcPadProtocol.h must stay a copy of the kit's")

    def test_kit_reference(self):
        mod = load(os.path.join(KIT, "ref", "test_smc_pad_protocol.py"), "test_smc_pad_protocol")
        result = unittest.TextTestRunner(stream=open(os.devnull, "w")).run(
            unittest.defaultTestLoader.loadTestsFromModule(mod))
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)

    def test_padcore_vectors_are_current(self):
        gen = load(os.path.join(TEST, "make_padcore_vectors.py"), "make_padcore_vectors")
        with open(os.path.join(TEST, "padcore_vectors.h")) as f:
            self.assertEqual(f.read(), gen.render(), "run sdk/ESPidiPad/extras/test/make_padcore_vectors.py")


@unittest.skipUnless(CXX, "no host C++ compiler")
class HostCppTest(unittest.TestCase):
    def build_and_run(self, source, include):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "t")
            build = subprocess.run([CXX, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-I", include, source, "-o", exe],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([exe], capture_output=True, text=True, cwd=os.path.dirname(source))
            self.assertEqual(run.returncode, 0, run.stdout)
            self.assertIn("all passed", run.stdout)

    def test_kit_header(self):
        self.build_and_run(os.path.join(KIT, "esp32", "test", "test_protocol.cpp"), os.path.join(KIT, "esp32"))

    def test_padcore(self):
        self.build_and_run(os.path.join(TEST, "test_padcore.cpp"), os.path.join(PAD, "src"))


if __name__ == "__main__":
    unittest.main()
