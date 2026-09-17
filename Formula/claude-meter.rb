class ClaudeMeter < Formula
  include Language::Python::Virtualenv

  desc "macOS menu bar tool that tracks Claude Code API spend in real time"
  homepage "https://github.com/headnoodle/claude-meter"
  url "https://github.com/headnoodle/claude-meter/archive/refs/tags/v0.4.4.tar.gz"
  sha256 "f07b71dcdc860ca38f2a85e192092183974cd9400dc7448664d7f860f1a0ecab"
  license "MIT"

  depends_on :macos
  depends_on "python@3.12"

  resource "pyobjc-core" do
    url "https://files.pythonhosted.org/packages/source/p/pyobjc_core/pyobjc_core-12.2.2.tar.gz"
    sha256 "3906452339cd06a3bb07df103c2511d4cb0f7a22d8771c0b802eba15d9a642b6"
  end

  resource "pyobjc-framework-Cocoa" do
    url "https://files.pythonhosted.org/packages/source/p/pyobjc_framework_cocoa/pyobjc_framework_cocoa-12.2.2.tar.gz"
    sha256 "c96c0ef69a71afbbb0e6a7d594b455c5fe47d62e0db376ee7a2b4b828c16ace9"
  end

  resource "rumps" do
    url "https://files.pythonhosted.org/packages/source/r/rumps/rumps-0.4.0.tar.gz"
    sha256 "17fb33c21b54b1e25db0d71d1d793dc19dc3c0b7d8c79dc6d833d0cffc8b1596"
  end

  def install
    libexec.install "monitor.py"
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install resources

    (bin/"claude-meter").write <<~EOS
      #!/bin/bash
      exec "#{libexec}/bin/python3" "#{libexec}/monitor.py" "$@"
    EOS
    chmod 0755, bin/"claude-meter"
  end

  def post_install
    # Restart the service if the user has it configured (plist exists).
    # brew upgrade stops the service before post_install runs, so we can't
    # rely on launchctl list — check for the plist file instead.
    plist_path = File.expand_path("~/Library/LaunchAgents/homebrew.mxcl.claude-meter.plist")
    return unless File.exist?(plist_path)

    target = "gui/#{Process.uid}"
    # Silently remove any stale entry, then start fresh with the new binary.
    system "/bin/launchctl", "bootout", target, plist_path, out: File::NULL, err: File::NULL
    system "/bin/launchctl", "bootstrap", target, plist_path
  rescue StandardError
    nil
  end

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

      Right-click the 🤖 menu bar icon for Settings and Refresh.

      To stop:
        brew services stop claude-meter
    EOS
  end

  test do
    assert_match "claude-meter #{version}", shell_output("#{bin}/claude-meter --version")
  end
end
