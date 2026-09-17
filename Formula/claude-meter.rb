class ClaudeMeter < Formula
  desc "macOS menu bar tool that tracks Claude Code API spend in real time"
  homepage "https://github.com/headnoodle/claude-meter"
  url "https://github.com/headnoodle/claude-meter/archive/refs/tags/v0.3.0.tar.gz"
  sha256 "66eb6732a585eb54f2443f6ac1e5c48079dfc1aa26773a5ed682fafeab63fa54"
  license "MIT"

  depends_on :macos
  depends_on "python@3.12"

  def install
    libexec.install "monitor.py"

    # Create a virtualenv and install rumps (pulls in pyobjc automatically)
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install "rumps"

    # Shim that runs monitor.py with the venv Python
    (bin/"claude-meter").write <<~EOS
      #!/bin/bash
      exec "#{libexec}/bin/python3" "#{libexec}/monitor.py" "$@"
    EOS
    chmod 0755, bin/"claude-meter"
  end

  # `brew services start claude-meter` creates a LaunchAgent that starts the
  # menu bar app on login. No Dock icon appears (rumps uses accessory policy).
  service do
    run        [opt_bin/"claude-meter"]
    keep_alive true
    log_path        var/"log/claude-meter.log"
    error_log_path  var/"log/claude-meter.log"
  end

  def caveats
    <<~EOS
      Start the menu bar app:
        brew services start claude-meter

      To set a daily budget, click the 🤖 menu bar icon → Preferences → Set Budget…
      The default is $50/day.

      To stop:
        brew services stop claude-meter
    EOS
  end

  test do
    assert_match "claude-meter #{version}", shell_output("#{bin}/claude-meter --version")
  end
end
