"""What KiCad's toolbar button runs (plugin.json's entry point): the kicad2ems window, with KiCad's API socket and
token in the environment. Also: python kicad2ems_plugin.py install | uninstall [--exec] (kicad2ems.ipc.install)."""
import sys

from kicad2ems.ipc.__main__ import main            # (kicad2ems is in the plugin's environment: requirements.txt)

if __name__ == '__main__':
    sys.exit(main())
