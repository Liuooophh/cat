// Pawpal's own helper. No installation or elevation is required.
// It outlives an abruptly terminated parent just long enough to stop only its ETW session.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

internal static class MetricsGuard {
    [StructLayout(LayoutKind.Sequential)]
    private struct WnodeHeader {
        public uint BufferSize, ProviderId;
        public ulong HistoricalContext, TimeStamp;
        public Guid Guid;
        public uint ClientContext, Flags;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct TraceProperties {
        public WnodeHeader Wnode;
        public uint BufferSize, MinimumBuffers, MaximumBuffers, MaximumFileSize, LogFileMode, FlushTimer, EnableFlags;
        public int AgeLimit;
        public uint NumberOfBuffers, FreeBuffers, EventsLost, BuffersWritten, LogBuffersLost, RealTimeBuffersLost;
        public IntPtr LoggerThreadId;
        public uint LogFileNameOffset, LoggerNameOffset;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inherit, uint processId);
    [DllImport("kernel32.dll")]
    private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode)]
    private static extern uint ControlTraceW(ulong session, string name, IntPtr properties, uint control);

    private static readonly object OutputLock = new object();

    private static void Forward(object sender, DataReceivedEventArgs args) {
        if (args.Data == null) return;
        try { lock (OutputLock) { Console.Out.WriteLine(args.Data); Console.Out.Flush(); } }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
    }

    private static void StopOwnSession(string name) {
        int size = Marshal.SizeOf(typeof(TraceProperties));
        IntPtr buffer = Marshal.AllocHGlobal(size + 2048);
        try {
            byte[] zeros = new byte[size + 2048];
            Marshal.Copy(zeros, 0, buffer, zeros.Length);
            TraceProperties properties = new TraceProperties();
            properties.Wnode.BufferSize = (uint)(size + 2048);
            properties.LoggerNameOffset = (uint)size;
            Marshal.StructureToPtr(properties, buffer, false);
            // EVENT_TRACE_CONTROL_STOP. The unpredictable name belongs solely to this parent.
            ControlTraceW(0, name, buffer, 1);
        } finally { Marshal.FreeHGlobal(buffer); }
    }

    private static int Main(string[] args) {
        uint parentPid;
        if (args.Length != 3 || !uint.TryParse(args[0], out parentPid)
                || !args[2].StartsWith("Pawpal-", StringComparison.Ordinal)
                || args[2].Length > 80 || !File.Exists(args[1])) return 2;
        IntPtr parent = OpenProcess(0x00100000, false, parentPid); // SYNCHRONIZE; no administrator privilege.
        if (parent == IntPtr.Zero) return 3;
        Process child = null;
        try {
            if (WaitForSingleObject(parent, 0) != 258) return 0; // WAIT_TIMEOUT means still alive.
            ProcessStartInfo info = new ProcessStartInfo(args[1]);
            info.Arguments = "--output_stdout --no_console_stats --no_track_gpu --no_track_input --v1_metrics --session_name " + args[2];
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.WorkingDirectory = Path.GetDirectoryName(args[1]);
            child = new Process();
            child.StartInfo = info;
            child.OutputDataReceived += Forward;
            child.ErrorDataReceived += Forward;
            child.Start();
            child.BeginOutputReadLine();
            child.BeginErrorReadLine();
            while (WaitForSingleObject(parent, 250) == 258 && !child.HasExited) { }
            StopOwnSession(args[2]);
            if (!child.WaitForExit(2000)) { child.Kill(); child.WaitForExit(1000); }
            child.WaitForExit(); // Flush asynchronous CSV forwarding.
            return 0;
        } catch (Exception exception) {
            try { Console.Error.WriteLine("error: metrics guardian " + exception.GetType().Name); } catch { }
            StopOwnSession(args[2]);
            if (child != null) { try { if (!child.HasExited) child.Kill(); } catch { } }
            return 4;
        } finally {
            if (child != null) child.Dispose();
            CloseHandle(parent);
        }
    }
}
