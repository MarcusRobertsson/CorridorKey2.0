"""Verify x64 Windows can load the OFX and its public registration ABI."""
import ctypes as C
from pathlib import Path
import sys

bundle = Path(sys.argv[1] if len(sys.argv) > 1 else
              "ofx/build-zig/CorridorKey2.ofx.bundle")
plugin = C.CDLL(str((bundle / "Contents/Win64/CorridorKey2.ofx").resolve()))


class OfxPlugin(C.Structure):
    _fields_ = [("api", C.c_char_p), ("apiVersion", C.c_int), ("identifier", C.c_char_p),
                ("major", C.c_uint), ("minor", C.c_uint),
                ("setHost", C.c_void_p), ("mainEntry", C.c_void_p)]


plugin.OfxGetPlugin.argtypes = [C.c_int]
plugin.OfxGetPlugin.restype = C.POINTER(OfxPlugin)
assert plugin.OfxGetNumberOfPlugins() == 1
info = plugin.OfxGetPlugin(0).contents
assert info.api == b"OfxImageEffectPluginAPI" and info.apiVersion == 1
assert info.identifier == b"org.corridorkey.community.v2" and info.major == 2
assert info.setHost and info.mainEntry
print(f"Loaded {info.identifier.decode()} v{info.major}.{info.minor}: OFX ABI OK")
