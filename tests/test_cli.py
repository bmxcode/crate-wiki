from typer.testing import CliRunner

from crate_wiki import __version__
from crate_wiki.cli import app

runner = CliRunner()


def test_version_flag_reports_the_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_names_the_tool():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "crate" in result.output
