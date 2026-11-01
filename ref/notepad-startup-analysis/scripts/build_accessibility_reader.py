"""Build the local read-only accessibility probe using installed Framework tools."""
from pathlib import Path
import os
import subprocess
from test_resources import limit_test_resources

out = Path(__file__).resolve().parent
limit_test_resources()
framework = Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319'
command = [str(framework/'csc.exe'), '/nologo', '/target:exe', '/platform:x64',
    '/out:'+str(out/'read_owned_accessibility.exe'), '/r:System.Xml.Linq.dll']
command.extend('/r:'+str(framework/'WPF'/name) for name in ['UIAutomationClient.dll', 'UIAutomationTypes.dll', 'WindowsBase.dll'])
command.append(str(out/'read_owned_accessibility.cs'))
print('Build read-only accessibility probe', flush=True)
subprocess.run(command, check=True)
print('BUILT', flush=True)
