// MAgent 启动器：双击即启动软件（转发给内嵌 Python 运行时）。
// 仅做进程转发——不承担 Python 解释器角色，因此不影响门禁脚本与解题代码执行。
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string exeDir = AppDomain.CurrentDomain.BaseDirectory;
        string pythonw = Path.Combine(exeDir, "runtime", "pythonw.exe");
        if (!File.Exists(pythonw))
        {
            MessageBox.Show(
                "找不到内嵌运行环境：\r\n" + pythonw +
                "\r\n\r\n请确认 MAgent.exe 与 runtime 文件夹在同一目录下（或重新安装）。",
                "MAgent 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        var psi = new ProcessStartInfo
        {
            FileName = pythonw,
            Arguments = "-m magent serve",
            WorkingDirectory = exeDir,
            UseShellExecute = false,
        };
        try
        {
            Process.Start(psi);
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show("启动失败：" + ex.Message, "MAgent", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}