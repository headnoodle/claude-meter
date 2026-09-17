class ClaudeMeter < Formula
  desc "macOS menu bar tool that tracks Claude Code API spend in real time"
  homepage "https://github.com/headnoodle/claude-meter"
  url "https://github.com/headnoodle/claude-meter/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "a1242d6add901d8d65efcaa18758c7f5ff3c2bff3e8c06475f2b7e0fdbda400b"
  license "MIT"

  depends_on :macos

  def install
    libexec.install "monitor.py"
    libexec.install "xbar-plugin/claude_tokens.1m.py"
    chmod 0755, libexec/"monitor.py"
    bin.install_symlink libexec/"monitor.py" => "claude-meter"
  end

  def caveats
    xbar_plugins = "#{Dir.home}/Library/Application Support/xbar/plugins"
    <<~EOS
      Symlink the xbar plugin to start tracking:
        mkdir -p "#{xbar_plugins}"
        ln -sf "#{libexec}/claude_tokens.1m.py" \\
               "#{xbar_plugins}/claude_tokens.1m.py"
        open -a xbar

      To set a daily budget (default $50), right-click the menu bar item
      and open xbar Settings.
    EOS
  end

  test do
    assert_match "claude-meter #{version}", shell_output("#{bin}/claude-meter --version")
  end
end
