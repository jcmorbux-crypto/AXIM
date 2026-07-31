# Reverses enable_autologon.ps1: turns off Windows auto sign-in and clears
# the LSA-stored password secret. Does not touch any AXIM Scheduled Task
# or state - only the Winlogon auto-logon configuration.
#
# Usage (elevated PowerShell): powershell -File scripts\disable_autologon.ps1

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must be run from an elevated (Run as Administrator) PowerShell. It was not."
    exit 1
}

Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class AximLsaSecretClear {
    [StructLayout(LayoutKind.Sequential)]
    private struct LSA_UNICODE_STRING {
        public ushort Length;
        public ushort MaximumLength;
        public IntPtr Buffer;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LSA_OBJECT_ATTRIBUTES {
        public int Length;
        public IntPtr RootDirectory;
        public IntPtr ObjectName;
        public int Attributes;
        public IntPtr SecurityDescriptor;
        public IntPtr SecurityQualityOfService;
    }

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern uint LsaOpenPolicy(ref LSA_UNICODE_STRING SystemName, ref LSA_OBJECT_ATTRIBUTES ObjectAttributes, uint DesiredAccess, out IntPtr PolicyHandle);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern uint LsaStorePrivateData(IntPtr PolicyHandle, ref LSA_UNICODE_STRING KeyName, IntPtr PrivateData);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern uint LsaClose(IntPtr ObjectHandle);

    private const uint POLICY_CREATE_SECRET = 0x00000020;
    private const uint POLICY_GET_PRIVATE_INFORMATION = 0x00000004;

    private static LSA_UNICODE_STRING ToLsaString(string s) {
        var lus = new LSA_UNICODE_STRING();
        lus.Buffer = Marshal.StringToHGlobalUni(s);
        lus.Length = (ushort)(s.Length * 2);
        lus.MaximumLength = (ushort)((s.Length + 1) * 2);
        return lus;
    }

    public static void ClearSecret(string keyName) {
        LSA_OBJECT_ATTRIBUTES oa = new LSA_OBJECT_ATTRIBUTES();
        LSA_UNICODE_STRING system = new LSA_UNICODE_STRING();
        IntPtr policyHandle;
        uint status = LsaOpenPolicy(ref system, ref oa, POLICY_CREATE_SECRET | POLICY_GET_PRIVATE_INFORMATION, out policyHandle);
        if (status != 0) throw new Exception("LsaOpenPolicy failed: 0x" + status.ToString("X8"));

        LSA_UNICODE_STRING lsaKey = ToLsaString(keyName);
        // NULL PrivateData deletes the secret.
        status = LsaStorePrivateData(policyHandle, ref lsaKey, IntPtr.Zero);
        LsaClose(policyHandle);
        if (status != 0) throw new Exception("LsaStorePrivateData (clear) failed: 0x" + status.ToString("X8"));
    }
}
'@

try {
    [AximLsaSecretClear]::ClearSecret("DefaultPassword")
    Write-Host "Cleared LSA secret 'DefaultPassword'."
} catch {
    Write-Host "Note: could not clear LSA secret (it may not have existed): $($_.Exception.Message)"
}

$winlogonKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $winlogonKey -Name AutoAdminLogon -Value "0" -Type String
Remove-ItemProperty -Path $winlogonKey -Name DefaultPassword -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Auto sign-in disabled. A future boot will require signing in manually again."
