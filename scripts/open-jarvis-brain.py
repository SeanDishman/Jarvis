from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop.memory import Memory
from desktop.preferences import Preferences
from desktop.obsidian import open_vault

memory = Memory(Preferences())
memory.setup()
open_vault(memory.vault)
