<#
    The daily Windows task that runs ytwarm on its own.

        .\schedule.ps1 install        # create it (default 10:00, up to 4h late)
        .\schedule.ps1 install -At 19:30 -WindowHours 3
        .\schedule.ps1 status
        .\schedule.ps1 pause          # stop it running, keep the task
        .\schedule.ps1 resume
        .\schedule.ps1 run-now        # run it this second, for testing
        .\schedule.ps1 remove

    Two settings are the whole point of using PowerShell here rather than
    schtasks.exe:

    - **RandomDelay.** The task fires at the same clock time every day, and then
      waits a random slice of the window before starting. Exactly 10:00:00 every
      morning is a machine; somewhere between 10:00 and 14:00 is a person.
    - **Interactive logon.** The task runs as you, only while you are signed in.
      It has to: a warming session opens a real Chrome window on your desktop,
      and a task running in session 0 would have nowhere to draw it.

    StartWhenAvailable covers the PC being off at the trigger time - the run
    happens once the machine is back, instead of being skipped silently.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "remove", "status", "pause", "resume", "run-now")]
    [string]$Command = "status",
    [string]$At = "10:00",
    [int]$WindowHours = 4
)

$ErrorActionPreference = "Stop"
$TaskName = "ytwarm daily warm"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Bat = Join-Path $Here "daily_warm.bat"

function Get-Task { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }

function Show-Status {
    $t = Get-Task
    if (-not $t) {
        Write-Host "Not installed. Run:  .\schedule.ps1 install"
        return
    }
    $i = Get-ScheduledTaskInfo $t
    $trigger = $t.Triggers[0]
    $state = if ($t.State -eq "Disabled") { "PAUSED" } else { "on ($($t.State))" }
    Write-Host "Task      : $TaskName"
    Write-Host "State     : $state"
    Write-Host "Fires at  : $($trigger.StartBoundary.Substring(11,5)) daily, plus a random delay of up to $($trigger.RandomDelay)"
    Write-Host "Next run  : $($i.NextRunTime)"
    Write-Host "Last run  : $($i.LastRunTime)   result $($i.LastTaskResult)"
}

switch ($Command) {
    "install" {
        if (-not (Test-Path $Bat)) { throw "missing $Bat" }
        $action = New-ScheduledTaskAction -Execute $Bat -WorkingDirectory $Here
        $trigger = New-ScheduledTaskTrigger -Daily -At $At
        # The CIM object wants an ISO 8601 duration, not a TimeSpan.
        $trigger.RandomDelay = "PT{0}H" -f $WindowHours
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
            -MultipleInstances IgnoreNew
        try {
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                -Principal $principal -Settings $settings -Force `
                -Description "Warms the ytwarm YouTube profiles once a day." | Out-Null
        } catch [System.UnauthorizedAccessException] {
            throw "Windows refused to create the task. Run this window as Administrator and try again."
        }
        Write-Host "Installed."
        Show-Status
    }
    "remove"  { if (Get-Task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; Write-Host "Removed." } else { Write-Host "Nothing to remove." } }
    "pause"   { if (Get-Task) { Disable-ScheduledTask -TaskName $TaskName | Out-Null; Write-Host "Paused - it will not run until you resume it." } else { Write-Host "Not installed." } }
    "resume"  { if (Get-Task) { Enable-ScheduledTask  -TaskName $TaskName | Out-Null; Write-Host "Running again." } else { Write-Host "Not installed." } }
    "run-now" { if (Get-Task) { Start-ScheduledTask   -TaskName $TaskName; Write-Host "Started. Watch logs\warm-all.log." } else { Write-Host "Not installed." } }
    default   { Show-Status }
}
