class ClaudeMeter < Formula
  include Language::Python::Virtualenv

  desc "macOS menu bar tool that tracks Claude Code API spend in real time"
  homepage "https://github.com/headnoodle/claude-meter"
  url "https://github.com/headnoodle/claude-meter/archive/refs/tags/v0.3.0.tar.gz"
  sha256 "66eb6732a585eb54f2443f6ac1e5c48079dfc1aa26773a5ed682fafeab63fa54"
  license "MIT"

  depends_on :macos
  depends_on "python@3.12"

  resource "pyobjc-core" do
    url "https://files.pythonhosted.org/packages/cp312/p/pyobjc_core/pyobjc_core-12.2.2-cp312-cp312-macosx_10_13_universal2.whl"
    sha256 "122e6ad302a2abf5d4d4adb0156db751600ddf2768441696cba17b31323085e7"
  end

  resource "pyobjc-framework-Cocoa" do
    url "https://files.pythonhosted.org/packages/cp312/p/pyobjc_framework_cocoa/pyobjc_framework_cocoa-12.2.2-cp312-cp312-macosx_10_13_universal2.whl"
    sha256 "e106f395531e67694376b0f1184612cbeea3ec8b9bf56b55ef41d026171d2a2d"
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
