# PyInstaller spec of the frozen agent (one file). Run by ``packaging/agent/build.py``, which
# freezes the workflow script process first and names its folder in ZYCODE_CHILD_DIR.
#
# Only the OpenAI-format provider is put in. The others (``langchain_anthropic``,
# ``langchain_deepseek``) are left out on purpose: a frozen build says so when one of them is
# chosen (``pi_agent_core.adapters.langchain_stream``).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

CHILD_DIR = os.environ["ZYCODE_CHILD_DIR"]

OUR_PACKAGES = [
    "pi_agent_core",
    "pi_agent_harness",
    "pi_agent_cli",
    "pi_web_access",
    "pi_goal_x",
    "pi_dynamic_workflows",
    "pi_mcp",
]
# Distributions whose entry points (group ``pi_agent.extensions``) and versions are read at run
# time through ``importlib.metadata``: the metadata has to be in the bundle.
OUR_DISTRIBUTIONS = [
    "pi-agent-core-lc",
    "pi-agent-harness-lc",
    "pi-agent-cli-lc",
    "pi-web-access-py",
    "pi-goal-x-py",
    "pi-dynamic-workflows-py",
    "pi-mcp-py",
]
# Packages that load part of themselves by name, so the analysis does not see it.
BY_NAME = ["langchain_openai", "langchain_core", "tiktoken_ext", "acp"]

hiddenimports = []
for package in [*OUR_PACKAGES, *BY_NAME]:
    hiddenimports += collect_submodules(
        package, filter=lambda name: ".tests.test_" not in name and not name.endswith(".conftest")
    )

datas = [(CHILD_DIR, "pi_dynamic_workflows/sandbox/frozen-child")]
for package in ("pi_dynamic_workflows", "pi_agent_cli", "pi_agent_core", "pi_web_access", "pi_goal_x"):
    datas += collect_data_files(package)
for distribution in OUR_DISTRIBUTIONS:
    datas += copy_metadata(distribution)

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=["langchain_anthropic", "langchain_deepseek", "pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="zycode-agent",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
