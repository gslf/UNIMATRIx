"""Assert actual file privacy using each operating system's permission model."""

import json
import os
import stat
import subprocess


def assert_private_file(path):
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        return
    # Inspect through the OS independently of the production ctypes implementation.
    script = """
        $ErrorActionPreference = 'Stop'
        $acl = Get-Acl -LiteralPath $env:UNIMATRIX_TEST_PRIVATE_PATH
        $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) |
            ForEach-Object { [pscustomobject]@{
                sid = $_.IdentityReference.Value
                rights = [int]$_.FileSystemRights
                inherited = $_.IsInherited
                type = [string]$_.AccessControlType
            } })
        [pscustomobject]@{
            protected = $acl.AreAccessRulesProtected
            user = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            rules = $rules
        } | ConvertTo-Json -Depth 4 -Compress
    """
    # Windows PowerShell must load its own modules, not a parent PowerShell 7's modules.
    environment = {key: value for key, value in os.environ.items() if key.lower() != "psmodulepath"}
    environment["UNIMATRIX_TEST_PRIVATE_PATH"] = str(path)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=environment,
        capture_output=True, text=True, check=False, creationflags=0x08000000,
    )
    assert result.returncode == 0, result.stderr
    acl = json.loads(result.stdout)
    assert acl["protected"]
    assert acl["rules"] == [dict(sid=acl["user"], rights=0x1F01FF, inherited=False, type="Allow")]
