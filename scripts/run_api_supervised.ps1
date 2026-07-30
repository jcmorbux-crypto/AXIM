# Supervises `python -m uvicorn api.main:app` with an unconditional restart
# loop - the API-process counterpart to run_listener_supervised.ps1. See
# that script's own header comment for why this exists (Task Scheduler's
# RestartOnFailure does not trigger on a forcibly-terminated process, and a
# Job Object is needed so killing this supervisor also kills its uvicorn
# child rather than orphaning it). Confirmed live this same audit: the
# previous plain-python.exe task action left the API down with no restart
# after a forced termination earlier today (LastTaskResult 4294967295, the
# same "successful completion" code the Listener script's header describes)
# - this wrapper closes that gap the same way it was already closed for the
# Listener.

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$LogFile = Join-Path $ProjectRoot "logs\api_supervisor.log"

$ApiBindHost = "127.0.0.1"
$ApiBindPort = "8090"
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*API_BIND_HOST\s*=\s*(.+?)\s*$') { $ApiBindHost = $Matches[1] }
        if ($_ -match '^\s*API_BIND_PORT\s*=\s*(.+?)\s*$') { $ApiBindPort = $Matches[1] }
    }
}

function Write-SupervisorLog($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class AximApiJob {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr CreateJobObject(IntPtr a, string lpName);

    [DllImport("kernel32.dll")]
    public static extern bool SetInformationJobObject(IntPtr hJob, int JobObjectInfoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public IntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    public const int JobObjectExtendedLimitInformation = 9;
    public const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

    public static IntPtr CreateKillOnCloseJob() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr ptr = Marshal.AllocHGlobal(length);
        Marshal.StructureToPtr(info, ptr, false);
        SetInformationJobObject(job, JobObjectExtendedLimitInformation, ptr, (uint)length);
        Marshal.FreeHGlobal(ptr);
        return job;
    }
}
'@

$AximApiJobHandle = [AximApiJob]::CreateKillOnCloseJob()

Write-SupervisorLog "api supervisor: started, watching uvicorn api.main:app on ${ApiBindHost}:${ApiBindPort}"

while ($true) {
    Write-SupervisorLog "api supervisor: launching uvicorn"
    $proc = Start-Process -FilePath $PythonExe `
        -ArgumentList "-m", "uvicorn", "api.main:app", "--host", $ApiBindHost, "--port", $ApiBindPort `
        -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
    [AximApiJob]::AssignProcessToJobObject($AximApiJobHandle, $proc.Handle) | Out-Null
    $proc.WaitForExit()
    Write-SupervisorLog "api supervisor: uvicorn exited with code $($proc.ExitCode) - restarting in 60s"
    Start-Sleep -Seconds 60
}
