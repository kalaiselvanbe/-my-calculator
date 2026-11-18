import ast
import ctypes
import dis
import io
import contextlib
from typing import Any, Callable

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Label, RichLog, TabbedContent, TabPane, TextArea

# =====================================================================
# 1. LOW-LEVEL AST & C-MEMORY ENGINE
# =====================================================================
class CallInterceptor(ast.NodeTransformer):
    """Rewrites every function/method call in the AST to route through a live telemetry hook."""
    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func_name = ast.unparse(node.func)
        return ast.Call(
            func=ast.Name(id="_live_hook", ctx=ast.Load()),
            args=[ast.Constant(value=func_name), node.func, *node.args],
            keywords=node.keywords,
        )

class PyObjectHeader(ctypes.Structure):
    """Maps directly onto CPython's underlying C struct in RAM."""
    _fields_ = [
        ("ob_refcnt", ctypes.c_ssize_t),
        ("ob_type", ctypes.c_void_p),
    ]

def inspect_c_memory(obj: Any) -> str:
    addr = id(obj)
    header = PyObjectHeader.from_address(addr)
    return (
        f"Object Type   : {type(obj).__name__}\n"
        f"RAM Address   : {hex(addr)}\n"
        f"C RefCount    : {header.ob_refcnt}\n"
        f"PyType Ptr    : {hex(header.ob_type)}"
    )

# =====================================================================
# 2. REACTIVE ASYNC TERMINAL UI (TEXTUAL)
# =====================================================================
DEFAULT_CODE = '''def process_payload(raw_text: str, tag: str) -> str:
    cleaned = raw_text.strip().lower()
    formatted = cleaned.replace(" ", "_")
    return f"[{tag.upper()}]::{formatted}"

# Entrypoint executed by the studio:
result = process_payload("  Secret Agent Data  ", "classified")
'''

class ASTStudioApp(App):
    CSS = """
    Screen {
        background: #0d1117;
    }
    #main-split {
        height: 1fr;
    }
    #left-pane {
        width: 48%;
        border: tall #30363d;
        padding: 1;
    }
    #right-pane {
        width: 52%;
        border: tall #30363d;
        padding: 1;
    }
    .pane-title {
        color: #58a6ff;
        text-style: bold;
        margin-bottom: 1;
    }
    TextArea {
        height: 1fr;
        border: round #21262d;
    }
    #run-btn {
        width: 100%;
        margin-top: 1;
        background: #238636;
        color: white;
        text-style: bold;
    }
    RichLog {
        background: #161b22;
        border: round #21262d;
        padding: 1;
        height: 1fr;
    }
    """

    BINDINGS = [("ctrl+r", "run_engine", "Compile & Hijack AST")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-split"):
            with Vertical(id="left-pane"):
                yield Label("1. LIVE PYTHON SOURCE (Editable)", classes="pane-title")
                yield TextArea.code_editor(DEFAULT_CODE, language="python", id="code-editor")
                yield Button("⚡ HIJACK AST & EXECUTE (Ctrl+R)", id="run-btn")

            with Vertical(id="right-pane"):
                yield Label("2. RUNTIME TELEMETRY & INTERNALS", classes="pane-title")
                with TabbedContent():
                    with TabPane("Execution & Hooks", id="tab-exec"):
                        yield RichLog(id="log-exec", markup=True)
                    with TabPane("Mutated AST", id="tab-ast"):
                        yield RichLog(id="log-ast", markup=True)
                    with TabPane("CPython Bytecode", id="tab-bytecode"):
                        yield RichLog(id="log-bytecode", markup=True)
                    with TabPane("Raw C-Memory", id="tab-memory"):
                        yield RichLog(id="log-memory", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "CPython AST & Bytecode Introspection Studio"
        self.action_run_engine()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-btn":
            self.action_run_engine()

    def action_run_engine(self) -> None:
        source_code = self.query_one("#code-editor", TextArea).text
        log_exec = self.query_one("#log-exec", RichLog)
        log_ast = self.query_one("#log-ast", RichLog)
        log_bc = self.query_one("#log-bytecode", RichLog)
        log_mem = self.query_one("#log-memory", RichLog)

        for log in (log_exec, log_ast, log_bc, log_mem):
            log.clear()

        try:
            # Step A: Parse & Transform AST
            raw_tree = ast.parse(source_code)
            mutated_tree = CallInterceptor().visit(raw_tree)
            ast.fix_missing_locations(mutated_tree)

            log_ast.write("[bold cyan]--- Rewritten Python Source Generated from AST ---[/]")
            log_ast.write(ast.unparse(mutated_tree))
            log_ast.write("\n[bold cyan]--- Raw AST Dump ---[/]")
            log_ast.write(ast.dump(mutated_tree, indent=2))

            # Step B: Compile to Code Object & Disassemble Bytecode
            code_obj = compile(mutated_tree, filename="<live_studio>", mode="exec")
            bc_buffer = io.StringIO()
            with contextlib.redirect_stdout(bc_buffer):
                dis.dis(code_obj)
            log_bc.write(bc_buffer.getvalue())

            # Step C: Execute with Live Call Interception
            def _live_hook(name: str, target: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
                log_exec.write(f"[bold yellow][HOOK][/] Intercepted [cyan]{name}[/] -> args={args}")
                return target(*args, **kwargs)

            sandbox_globals: dict[str, Any] = {"__builtins__": __builtins__, "_live_hook": _live_hook}
            exec(code_obj, sandbox_globals)

            final_result = sandbox_globals.get("result", "<No 'result' variable assigned>")
            log_exec.write(f"\n[bold green]✔ FINAL OUTPUT:[/] {final_result!r}")

            # Step D: Inspect C-Level RAM Header of Output Object
            log_mem.write("[bold magenta]--- CPython PyObject Struct in RAM ---[/]")
            log_mem.write(inspect_c_memory(final_result))

        except Exception as exc:
            log_exec.write(f"[bold red]✖ COMPILATION / RUNTIME ERROR:[/] {exc}")

if __name__ == "__main__":
    ASTStudioApp().run()
