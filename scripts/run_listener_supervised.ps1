# Supervises core/telegram_listener.py with an unconditional restart loop.
#
# Why this exists (found via live-fire testing, not theory): Windows Task
# Scheduler's own RestartOnFailure setting does NOT trigger when a process
# is forcibly terminated (TerminateProcess - what Stop-Process -Force, a
# real OOM-kill, or a native crash all produce). Task Scheduler logs that
# exit as "successfully completed ... with return code 4294967295" (event
# ID 201, Information level, not a failure) - so RestartOnFailure, which
# only fires on a recognized failure exit, silently never engages for
# exactly the crash scenarios this supervision was built for. Confirmed
# twice: killed the Task-Scheduler-launched process directly, waited well
# past the configured 1-minute restart interval both times, no relaunch.
#
# This script is now the Scheduled Task's action instead of python.exe
# directly - a plain loop restarts the child on ANY exit (clean or
# forced), independent of how Task Scheduler classifies the exit code.
# run_forever() (core/telegram_listener.py) already handles in-process
# recovery (browser crash, worker pool rebuild, resumed trades); this is
# only the outer layer for when the whole Python process itself dies.
#
# Job Object (confirmed necessary live, not theoretical - see
# docs/AXIM_RELEASE_CHECKLIST.md's browser crash investigation):
# Stop-ScheduledTask on the "AXIM Listener" task stops TASK SCHEDULER'S OWN
# TRACKING of this script, but does not reliably terminate python.exe
# itself - Start-Process's child is an ordinary orphan-on-parent-death
# process on Windows, not automatically killed just because this script
# is. Confirmed: a Stop-ScheduledTask left python.exe (and its own
# chrome.exe) running for nearly a full day, invisible to Task Scheduler,
# fighting a freshly-restarted second instance over the same browser
# profile lock - repeatedly popping visible Chrome windows on the real
# desktop. Assigning python.exe to a Job Object with
# JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE fixes the case that matters most (the
# whole supervisor being killed): Windows automatically closes every handle
# a terminated process held, including the job handle held here, and that
# closure cascades a forced kill to every process in the job - python.exe
# and (since child processes join their parent's job by default, unless
# explicitly created with CREATE_BREAKAWAY_FROM_JOB, which neither Python
# nor Playwright does) chrome.exe and all of chrome.exe's own child
# processes. The explicit chrome.exe cleanup after -Wait below covers the
# other case (python.exe itself exits/crashes without the supervisor
# dying) - the job handle stays open in that case, so kill-on-close alone
# wouldn't reach an orphaned chrome.exe left behind by python.exe's own
# unclean exit.

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$LogFile = Join-Path $ProjectRoot "logs\supervisor.log"

function Write-SupervisorLog($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class AximJob {
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

# One job for this supervisor's whole lifetime - closed automatically (by
# Windows) whenever this powershell.exe process ends, for any reason,
# including a forced Stop-ScheduledTask kill.
$AximJobHandle = [AximJob]::CreateKillOnCloseJob()

function Stop-StraySessionChrome {
    # Defense-in-depth cleanup for the case where python.exe itself exits
    # (cleanly or crashed) without the supervisor dying - the Job Object
    # above only cascades a kill when THIS script's own process ends, so a
    # chrome.exe left behind by python.exe's own unclean exit needs this
    # explicit sweep instead. Matches execution/browser_session.py's own
    # per-launch guard (belt and suspenders, not a substitute for it - this
    # one runs once per restart cycle, that one runs on every single launch
    # attempt including in-process reconnects this script never sees).
    Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*AXIM\sessions\*' } |
        ForEach-Object {
            Write-SupervisorLog "supervisor: cleaning up stray chrome.exe pid=$($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

Write-SupervisorLog "supervisor: started, watching core\telegram_listener.py"
Stop-StraySessionChrome

while ($true) {
    Write-SupervisorLog "supervisor: launching telegram_listener.py"
    $proc = Start-Process -FilePath $PythonExe -ArgumentList "core\telegram_listener.py" `
        -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
    [AximJob]::AssignProcessToJobObject($AximJobHandle, $proc.Handle) | Out-Null
    $proc.WaitForExit()
    Write-SupervisorLog "supervisor: telegram_listener.py exited with code $($proc.ExitCode) - restarting in 60s"
    Stop-StraySessionChrome
    Start-Sleep -Seconds 60
}
