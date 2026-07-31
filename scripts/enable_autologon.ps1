# Enables Windows auto sign-in for THIS account (2026-07-29/30 RC1 reboot-
# survival audit) so the AtLogon-triggered "AXIM API"/"AXIM Listener"
# Scheduled Tasks - and the AtLogon-registered VS Code Tunnel - actually
# come up after an unattended reboot. An AtStartup (no-logon) trigger was
# considered and rejected: AXIM's Pocket Option automation runs a headed
# (non-headless) Chrome browser that must render on a real interactive
# desktop (execution/browser_session.py, headless=False) - a task running
# "whether user is logged on or not" executes in Session 0 with no desktop
# and could not display it. An interactive logon is a hard requirement, so
# closing the reboot-survival gap means automating that logon, not
# replacing it.
#
# SECURITY TRADEOFF (accept this deliberately, not by default): once
# enabled, anyone with physical or remote-desktop access to this machine's
# login screen bypasses the account password entirely and lands on this
# account's live desktop. Only run this if you specifically want that
# tradeoff for reboot-survival. Reverse with disable_autologon.ps1.
#
# Uses the same secure mechanism Sysinternals Autologon.exe uses - the
# password is stored as an LSA secret (DefaultPassword) via
# LsaStorePrivateData, NOT as a plaintext HKLM registry value. The
# registry only records AutoAdminLogon=1 and the username/domain; Windows
# reads the actual password from the LSA secret store at boot.
#
# Must run elevated (writes HKLM and LSA secrets). Prompts for the
# account's own Windows password interactively - never pass it as a
# script argument or paste it into a chat/log, since that would defeat
# the point of using LSA secret storage over a plaintext registry value.
#
# Usage (elevated PowerShell): powershell -File scripts\enable_autologon.ps1
# Reverse with:                powershell -File scripts\disable_autologon.ps1

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must be run from an elevated (Run as Administrator) PowerShell. It was not."
    exit 1
}

$UserName = $env:USERNAME
$DomainName = $env:COMPUTERNAME

Write-Host "This will enable automatic Windows sign-in for '$DomainName\$UserName' on every boot."
Write-Host "Anyone with access to this machine's login screen will land directly on this account's desktop."
$confirm = Read-Host "Type YES to continue"
if ($confirm -ne "YES") {
    Write-Host "Aborted - no changes made."
    exit 0
}

$securePassword = Read-Host "Enter the Windows password for $UserName" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
$plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class AximLsaSecret {
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
    private static extern uint LsaStorePrivateData(IntPtr PolicyHandle, ref LSA_UNICODE_STRING KeyName, ref LSA_UNICODE_STRING PrivateData);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern uint LsaClose(IntPtr ObjectHandle);

    private const uint POLICY_CREATE_SECRET = 0x00000020;
    private const uint POLICY_GET_PRIVATE_INFORMATION = 0x00000004;

    private static LSA_UNICODE_STRING ToLsaString(string s) {
        var lus = new LSA_UNICODE_STRING();
        if (s == null) s = "";
        lus.Buffer = Marshal.StringToHGlobalUni(s);
        lus.Length = (ushort)(s.Length * 2);
        lus.MaximumLength = (ushort)((s.Length + 1) * 2);
        return lus;
    }

    // Passing null/empty data clears the secret (used by the disable script).
    public static void SetSecret(string keyName, string value) {
        LSA_OBJECT_ATTRIBUTES oa = new LSA_OBJECT_ATTRIBUTES();
        LSA_UNICODE_STRING system = new LSA_UNICODE_STRING();
        IntPtr policyHandle;
        uint status = LsaOpenPolicy(ref system, ref oa, POLICY_CREATE_SECRET | POLICY_GET_PRIVATE_INFORMATION, out policyHandle);
        if (status != 0) throw new Exception("LsaOpenPolicy failed: 0x" + status.ToString("X8"));

        LSA_UNICODE_STRING lsaKey = ToLsaString(keyName);
        LSA_UNICODE_STRING lsaData = ToLsaString(value);
        status = LsaStorePrivateData(policyHandle, ref lsaKey, ref lsaData);
        LsaClose(policyHandle);
        if (status != 0) throw new Exception("LsaStorePrivateData failed: 0x" + status.ToString("X8"));
    }
}
'@

try {
    [AximLsaSecret]::SetSecret("DefaultPassword", $plainPassword)
    Write-Host "Password stored as LSA secret 'DefaultPassword'."
} finally {
    $plainPassword = $null
    [System.GC]::Collect()
}

$winlogonKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $winlogonKey -Name AutoAdminLogon -Value "1" -Type String
Set-ItemProperty -Path $winlogonKey -Name DefaultUserName -Value $UserName -Type String
Set-ItemProperty -Path $winlogonKey -Name DefaultDomainName -Value $DomainName -Type String
# Deliberately not setting DefaultPassword here - the LSA secret above is
# what Windows actually reads; leaving/removing this keeps no plaintext
# copy of the password in the registry.
Remove-ItemProperty -Path $winlogonKey -Name DefaultPassword -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Auto sign-in enabled for $DomainName\$UserName."
Write-Host "Verify with: Get-ItemProperty '$winlogonKey' | Select-Object AutoAdminLogon,DefaultUserName,DefaultDomainName"
Write-Host "Reverse with: powershell -File scripts\disable_autologon.ps1"
