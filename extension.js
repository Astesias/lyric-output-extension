const vscode = require('vscode');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let currentProcess = null;
let statusBarItem = null;
let outputChannel = null;
let lastSongTitle = '';

function activate(context) {

    // ── 状态栏组件（在右下角 copilot 旁边） ──
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
    statusBarItem.text = '🎵启动';
    statusBarItem.tooltip = '点击启动歌词同步';
    statusBarItem.command = 'lyric-output.toggle';
    statusBarItem.show();

    // ── 启动/停止切换 ──
    function start() {
        const config = vscode.workspace.getConfiguration('lyric-output');
        const scriptPath = path.join(__dirname, 'main4.py');
        const pythonPath = config.get('pythonPath') || 'C:/ProgramData/Anaconda/python.exe';
        if (!fs.existsSync(scriptPath) || !fs.existsSync(pythonPath)) return;

        // 写入 config.json（Cookie 等配置）
        const cookie = config.get('cookie') || '';
        const csrfToken = config.get('csrfToken') || '';
        const userAgent = config.get('user_agent') || '';
        const timeOffset = config.get('timeOffset', -0.6);
        const timerMode = config.get('timerMode', 'smtc');
        const showTitleInLyrics = config.get('showTitleInLyrics', false);
        const showTitleInStatusBar = config.get('showTitleInStatusBar', true);
        const cfgPath = path.join(__dirname, 'config.json');
        let cfg = {};
        if (fs.existsSync(cfgPath)) {
            try { cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf-8')); } catch {}
        }
        if (cookie) cfg.cookie = cookie;
        if (csrfToken) cfg.csrf_token = csrfToken;
        if (userAgent) cfg.user_agent = userAgent;
        cfg.time_offset = timeOffset;
        cfg.timer_mode = timerMode;
        cfg.show_title_in_lyrics = showTitleInLyrics;
        fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 4), 'utf-8');

        // 停止旧进程
        if (currentProcess) { currentProcess.kill(); currentProcess = null; }
        lastSongTitle = '';

        // 创建/清空输出频道
        if (outputChannel) outputChannel.dispose();
        outputChannel = vscode.window.createOutputChannel('🎵 网易云歌词');
        outputChannel.show();

        statusBarItem.text = '🎵 连接中...';
        statusBarItem.tooltip = '正在启动歌词同步';

        currentProcess = spawn(pythonPath, [scriptPath, '--plain'], { cwd: __dirname });

        // 处理 stderr（Python 错误输出）
        currentProcess.stderr.on('data', (data) => {
            const errText = data.toString('utf-8');
            if (outputChannel) {
                outputChannel.append(`[错误] ${errText}`);
            }
        });

        currentProcess.stdout.on('data', (data) => {
            let raw = data.toString('utf-8');

            // 检测到清屏指令 → 清空输出面板
            if (raw.includes('\x1b[2J') && outputChannel) {
                outputChannel.clear();
            }

            // 去除 ANSI 转义码
            const text = raw
                .replace(/\x1B\[[0-?9;]*[a-zA-Z]/g, '')
                .replace(/\x1B\][0-;]*\x07/g, '')
                .replace(/\x1B\[2J\x1B\[H/g, '');

            // 输出到面板
            if (text.trim() && outputChannel) outputChannel.append(text);

            // 从 "🎵 歌名" 中提取歌名更新状态栏
            const firstLine = text.split('\n')[0];
            const m = firstLine.match(/^🎵 (.+)/);
            if (m) {
                const title = m[1].trim();
                if (title !== lastSongTitle) {
                    lastSongTitle = title;
                    if (showTitleInStatusBar) {
                        statusBarItem.text = `🎵 ${title}`;
                        statusBarItem.tooltip = '点击停止歌词同步';
                    }
                }
            }
        });

        currentProcess.on('exit', (code) => {
            currentProcess = null;
            statusBarItem.text = '🎵 启动';
            statusBarItem.tooltip = code ? `进程异常退出 (code=${code})` : '点击启动歌词同步';
        });
        currentProcess.on('error', (err) => {
            currentProcess = null;
            statusBarItem.text = '🎵 启动';
            statusBarItem.tooltip = `启动失败: ${err.message}`;
        });
    }

    function stop() {
        if (currentProcess) {
            currentProcess.kill();
            currentProcess = null;
        }
        lastSongTitle = '';
        statusBarItem.text = '🎵 启动';
        statusBarItem.tooltip = '点击启动歌词同步';
    }

    // ── 命令：切换 ──
    const toggleCommand = vscode.commands.registerCommand('lyric-output.toggle', () => {
        if (currentProcess) stop();
        else start();
    });

    context.subscriptions.push(toggleCommand, statusBarItem);
}

function deactivate() {
    if (currentProcess) { currentProcess.kill(); currentProcess = null; }
    if (outputChannel) { outputChannel.dispose(); outputChannel = null; }
    if (statusBarItem) { statusBarItem.dispose(); statusBarItem = null; }
}

module.exports = { activate, deactivate };