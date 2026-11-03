import os, sys
sys.path[0] = os.path.realpath(sys.path[0])
from agent_skills.cli import main
raise SystemExit(main())
