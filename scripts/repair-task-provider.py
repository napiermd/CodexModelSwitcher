#!/usr/bin/env python3
"""Preview or repair incompatible Codex task routes using the bundled repair engine."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ModelHarbor/Support'))
from task_repair import main

if __name__ == '__main__':
    main()
