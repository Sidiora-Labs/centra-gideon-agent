import json
import sys
import time
from gideon.workspace.capabilities.workspace.ports import PortRegistry

registry = PortRegistry(sys.argv[1],allowed_ports=[int(sys.argv[2])])
print(json.dumps(registry.reserve({'project_id':'crash-app','request_id':'crash'})),flush=True)
time.sleep(60)
