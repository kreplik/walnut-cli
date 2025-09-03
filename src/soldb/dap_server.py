import asyncio
import json
import sys
import os
import time
import socket
import threading
import io
import contextlib
from typing import Dict, Any, List, Optional

from .evm_repl import EVMDebugger
from .auto_deploy import AutoDeployDebugger
from .colors import info

CRLF = b"\r\n"

class CaptureOutput:
    """Context manager to capture stdout and redirect to DAP output events."""
    def __init__(self, dap_server):
        self.dap_server = dap_server
        self.original_stdout = None
        self.captured_output = io.StringIO()
        
    def __enter__(self):
        self.original_stdout = sys.stdout
        sys.stdout = self.captured_output
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore original stdout
        sys.stdout = self.original_stdout
        
        # Send captured output to VS Code
        output = self.captured_output.getvalue()
        if output:
            self.dap_server._send_output(output)
        
        self.captured_output.close()

class WalnutDAPServer:
    """Debug Adapter Protocol server for walnut-cli (stdio version)"""
    def __init__(self):
        self._seq = 1
        self.debugger: Optional[EVMDebugger] = None
        self.session: Optional[AutoDeployDebugger] = None
        self.breakpoints: Dict[str, List[int]] = {}  # sourcePath -> [lines]
        self.thread_id = 1  # single-threaded
        self.log_sock = None

    def _capture_output(self):
        """Context manager to capture stdout and send as DAP output events."""
        return CaptureOutput(self)

    def _send_output(self, text: str, category: str = "stdout"):
        """Send output to VS Code via DAP output event."""
        self._event("output", {
            "output": text,
            "category": category  # "stdout", "stderr", "console"
        })

    # ---- DAP transport helpers ----
    def _send(self, msg: Dict[str, Any]):
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}".encode("utf-8") + CRLF + CRLF
        sys.stdout.buffer.write(header + body)
        sys.stdout.buffer.flush()

    def _event(self, event: str, body: Optional[Dict[str, Any]] = None):
        evt = {
            "type": "event",
            "seq": self._seq,
            "event": event,
            "body": body or {}
        }
        self._seq += 1
        self._send(evt)

    def _response(self, request: Dict[str, Any], success: bool = True, body: Optional[Dict[str, Any]] = None, message: Optional[str] = None):
        resp = {
            "type": "response",
            "seq": self._seq,
            "request_seq": request.get("seq"),
            "success": success,
            "command": request.get("command"),
        }
        if body is not None:
            resp["body"] = body
        if message and not success:
            resp["message"] = message
        self._seq += 1
        self._send(resp)

    def _read(self) -> Optional[Dict[str, Any]]:
        # Parse DAP headers from stdin
        content_length = None
        while True:
            line = sys.stdin.buffer.readline()
            if not line or line == b"\r\n":
                break
            k, _, v = line.partition(b":")
            if k.lower() == b"content-length":
                content_length = int(v.strip())
        if content_length is None:
            return None
        body = sys.stdin.buffer.read(content_length)
        return json.loads(body.decode("utf-8"))

    # ---- DAP request handlers ----
    def initialize(self, request):
        caps = {
            "supportsConfigurationDoneRequest": True,
            "supportsSetBreakpointsRequest": True,
            "supportsTerminateRequest": True,
            "supportsEvaluateForHovers": False,
            "supportsStepInTargetsRequest": False,
            "supportsStepBack": False,
            "supportsDataBreakpoints": False,
            "supportsCompletionsRequest": False,
            "supportsExceptionInfoRequest": False,
        }
        self._response(request, True, {"capabilities": caps})
        self._event("initialized")

    def launch(self, request):
        self.log("Launch request received")
        args = request.get("arguments", {}) or {}
        # Arguments (VS Code launch.json)
        contract_file = args.get("contractFile")
        contract_address = args.get("contractAddress")
        ethdebug_dir = args.get("ethdebugDir")
        rpc = args.get("rpc", "http://localhost:8545")
        constructor_args = args.get("constructorArgs", [])
        fork_url = args.get("forkUrl")
        fork_block = args.get("forkBlock")
        fork_port = int(args.get("forkPort", 8545))
        reuse_fork = bool(args.get("reuseFork", False))
        keep_fork = bool(args.get("keepFork", False))
        no_snapshot = bool(args.get("noSnapshot", False))
        
        # Handle function signature and args (support both old and new format)
        function_signature = args.get("function_signature") or args.get("function")
        function_args = args.get("function_args") or args.get("functionArgs", [])
       
        try:
            if contract_file:
                # Auto deploy + compile
                self.session = AutoDeployDebugger(
                    contract_file=contract_file,
                    rpc_url=rpc,
                    constructor_args=constructor_args,
                    fork_url=fork_url if fork_url else None,
                    fork_block=int(fork_block) if fork_block else None,
                    fork_port=fork_port,
                    reuse_fork=reuse_fork,
                    keep_fork=keep_fork,
                    auto_snapshot=not no_snapshot,
                )
                contract_address = self.session.contract_address
                ethdebug_dir = str(self.session.debug_dir)
                abi_path = str(self.session.abi_path)
                rpc = self.session.rpc_url
            elif contract_address and ethdebug_dir:
                abi_path = None
            else:
                raise ValueError("Provide contractFile or contractAddress + ethdebugDir")

            # Create EVMDebugger
            abi_path_param = abi_path or args.get("abiPath") or ""
            self.debugger = EVMDebugger(
                contract_address=str(contract_address),
                rpc_url=rpc,
                ethdebug_dir=ethdebug_dir,
                function_name=function_signature or "",
                function_args=function_args or [],
                interactive_mode=True,
                abi_path=abi_path_param,
            )
           
            # Explicitly load ABI if we have an abi_path
            if abi_path and hasattr(self.debugger, "tracer"):
                try:
                    self.debugger.tracer.load_abi(abi_path)
                    self.log(f"Loaded ABI from: {abi_path}")
                except Exception as e:
                    self.log(f"Warning: Failed to load ABI from {abi_path}: {e}")

            # Optional: pre-snapshot baseline
            if not no_snapshot and hasattr(self.debugger, "tracer"):
                try:
                    self.debugger.tracer.snapshot_state()
                except Exception:
                    pass
            self.log(f"Debugger initialized: {contract_address}")
            
            # Generate trace immediately during launch
            self.log(f"Running simulation for function: {self.debugger.function_name}")
            self.log(f"Contract address: {self.debugger.contract_address}")
            self.log(f"Function args: {self.debugger.function_args}")
            
            try:
                # Check prerequisites before simulation
                if not self.debugger.contract_address:
                    raise RuntimeError("No contract address available for simulation")
                
                if not self.debugger.function_name:
                    raise RuntimeError("No function name specified for debugging")
                
                # Check if ABI is loaded
                if hasattr(self.debugger.tracer, 'function_abis_by_name'):
                    available_functions = list(self.debugger.tracer.function_abis_by_name.keys())
                    self.log(f"Available functions in ABI: {available_functions}")
                    if self.debugger.function_name not in available_functions:
                        self.log(f"Function '{self.debugger.function_name}' not found in ABI")
                else:
                    self.log("No ABI information loaded in tracer")
                
                self.log("Calling _do_interactive()...")
                
                # Capture stdout from the debugger simulation
                with self._capture_output():
                    self.debugger._do_interactive()
                    
                self.log("_do_interactive() completed")
                
                if not self.debugger.current_trace:
                    raise RuntimeError("Simulation failed to generate trace - check function name and arguments")
                
                self.log(f"Trace generated with {len(self.debugger.current_trace.steps)} steps")
                
                # Check if function_trace was created
                if hasattr(self.debugger, 'function_trace'):
                    self.log(f"Function trace has {len(self.debugger.function_trace)} functions")
                    for i, func in enumerate(self.debugger.function_trace):
                        self.log(f"  Function {i}: {func.name} (entry: {func.entry_step}, exit: {func.exit_step})")
                else:
                    self.log("No function_trace attribute found")
                
                # Find the actual function entry point (first function call after dispatcher)
                entry_step = 0
                if hasattr(self.debugger, 'function_trace') and len(self.debugger.function_trace) > 0:
                    # Look for the target function by name
                    target_function = None
                    for func in self.debugger.function_trace:
                        if func.name == self.debugger.function_name:
                            target_function = func
                            break
                    
                    if target_function:
                        entry_step = target_function.entry_step
                        self.debugger.current_function = target_function
                        self.log(f"Found target function '{self.debugger.function_name}' at step {entry_step}")
                    elif len(self.debugger.function_trace) > 1:
                        # Skip dispatcher, go to first actual function
                        entry_step = self.debugger.function_trace[1].entry_step
                        self.debugger.current_function = self.debugger.function_trace[1]
                        self.log(f"Using first non-dispatcher function at step {entry_step}")
                    else:
                        # Use first function if only one exists
                        entry_step = self.debugger.function_trace[0].entry_step
                        self.debugger.current_function = self.debugger.function_trace[0]
                        self.log(f"Using only function at step {entry_step}")
                else:
                    self.log("No function trace found, starting at step 0")
                    
                # Set debugger to function entry point
                self.debugger.current_step = entry_step
                
                trace_length = len(self.debugger.current_trace.steps)
                self.log(f"Simulation complete: {trace_length} steps, starting at step {entry_step}")
                
                # Log current step details
                if entry_step < len(self.debugger.current_trace.steps):
                    current_step_info = self.debugger.current_trace.steps[entry_step]
                    self.log(f"Entry step {entry_step}: PC={current_step_info.pc}, OP={current_step_info.op}")
                
                
            except Exception as e:
                self.log(f"Simulation failed: {e}")
                raise RuntimeError(f"Failed to generate execution trace: {e}")
            
            self._response(request, True, {})
            self._event("thread", {"reason": "started", "threadId": self.thread_id})
            # Stop at function entry point with trace already available
            self.log("Sending stopped event with reason: entry")
            self._event("stopped", {"reason": "entry", "threadId": self.thread_id})
        except Exception as e:
            self._response(request, False, message=str(e))

    def setBreakpoints(self, request):
        args = request.get("arguments", {}) or {}
        src = args.get("source", {}) or {}
        path = src.get("path") or ""
        breakpoints = args.get("breakpoints", [])
        lines = []
        functions = []

        # Separate line and function breakpoints
        for bp in breakpoints:
            if "line" in bp:
                lines.append(bp["line"])
            if "functionName" in bp:
                functions.append(bp["functionName"])

        self.breakpoints[path] = lines[:]
        verified = []

        # Register line breakpoints in EVMDebugger
        if self.debugger:
            for line in lines:
                try:
                    self.debugger.do_break(f"{path}:{line}")
                    verified.append({"verified": True, "line": line})
                except Exception:
                    verified.append({"verified": False, "line": line})

            # Register function name breakpoints in EVMDebugger
            for func_name in functions:
                try:
                    self.debugger.do_break(func_name)
                    verified.append({"verified": True, "functionName": func_name})
                except Exception:
                    verified.append({"verified": False, "functionName": func_name})

        self._response(request, True, {"breakpoints": verified})

    def threads(self, request):
        self._response(request, True, {"threads": [{"id": self.thread_id, "name": "main"}]})

    def continue_(self, request):
        self.log("Continue request received")
        
        # Run until end or breakpoint
        if not self.debugger or not self.debugger.current_trace:
            self.log("No debugger or trace available")
            self._response(request, True, {})
            self._event("stopped", {"reason": "breakpoint", "threadId": self.thread_id})
            return

        trace = self.debugger.current_trace
        bps = getattr(self.debugger, "breakpoints", set())
        
        self.log(f"Continuing from step {self.debugger.current_step}/{len(trace.steps)-1}")
        self.log(f"Breakpoints: {bps}")
        
        start_step = self.debugger.current_step
        while self.debugger.current_step < len(trace.steps) - 1:
            self.debugger.current_step += 1
            pc = trace.steps[self.debugger.current_step].pc
            if pc in bps:
                self.log(f"Hit breakpoint at step {self.debugger.current_step}, PC {pc}")
                break

        if self.debugger.current_step == start_step:
            self.log("No steps taken - already at end or no valid steps")
        else:
            self.log(f"Moved from step {start_step} to {self.debugger.current_step}")

        self._response(request, True, {"allThreadsContinued": False})
        self._event("stopped", {"reason": "breakpoint", "threadId": self.thread_id})

    def next(self, request):
        # Source-level step
        try:
            if self.debugger:
                self.debugger.do_next("")
            self._response(request, True, {})
            self._event("stopped", {"reason": "step", "threadId": self.thread_id})
        except Exception as e:
            self._response(request, False, message=str(e))

    def stepIn(self, request):
        # Instruction-level step-in (fallback to instruction)
        try:
            if self.debugger:
                self.debugger.do_nexti("")
            self._response(request, True, {})
            self._event("stopped", {"reason": "step", "threadId": self.thread_id})
        except Exception as e:
            self._response(request, False, message=str(e))

    def stepOut(self, request):
        # Simple fallback: one source step
        return self.next(request)

    def stackTrace(self, request):
        if not self.debugger or not self.debugger.current_trace:
            return self._response(request, True, {"stackFrames": []})

        # Fallback to original implementation for now
        step = self.debugger.current_trace.steps[self.debugger.current_step]
        pc = step.pc
        func_name = getattr(self.debugger, "current_function", None)
        name = getattr(func_name, "name", None) or f"pc:{pc}"

        # Try to resolve source location from ETHDebug
        source = None
        line = 0
        col = 0
        parser = getattr(self.debugger.tracer, "ethdebug_parser", None)
        info_obj = getattr(self.debugger.tracer, "ethdebug_info", None)
        if info_obj and parser:
            si = info_obj.get_source_info(pc)
            if si:
                source_path, offset, _ = si
                l, c = parser.offset_to_line_col(source_path, offset)
                source = {"name": os.path.basename(source_path), "path": source_path}
                line, col = l, c

        frame = {
            "id": 1,
            "name": name,
            "line": line or 1,
            "column": col or 1,
            "source": source or {},
        }
        self._response(request, True, {"stackFrames": [frame], "totalFrames": 1})

    def scopes(self, request):
        scopes = [
            {"name": "Locals", "variablesReference": 1001, "expensive": False},
            {"name": "Stack", "variablesReference": 1002, "expensive": False},
            {"name": "Storage", "variablesReference": 1003, "expensive": False}
        ]
        self._response(request, True, {"scopes": scopes})

    def variables(self, request):
        ref = request.get("arguments", {}).get("variablesReference")
        vars_list: List[Dict[str, Any]] = []
        if not self.debugger or not self.debugger.current_trace:
            return self._response(request, True, {"variables": vars_list})

        step = self.debugger.current_trace.steps[self.debugger.current_step]
        pc = step.pc
        info_obj = self.debugger.tracer.ethdebug_info

        #self.log(f"info_obj.variable_locations: {info_obj.variable_locations}")
        
        if ref == 1001:
            # Locals from ETHDebug symbols
            
            if info_obj:
                vars_here = info_obj.get_variables_at_pc(pc)
                
                for var in vars_here:
                    value = None
                    location_str = f"{var.location_type}[{var.offset}]"
                    # Use your debugger's logic to extract the value
                    if var.location_type == "stack" and var.offset < len(step.stack):
                        raw_value = step.stack[var.offset]
                        value = self.debugger.tracer.decode_value(raw_value, var.type)
                    elif var.location_type == "memory" and step.memory:
                        value = self.debugger.tracer.extract_from_memory(step.memory, var.offset, var.type)
                    elif var.location_type == "storage" and step.storage:
                        value = self.debugger.tracer.extract_from_storage(step.storage, var.offset, var.type)
                    vars_list.append({
                        "name": var.name,
                        "value": str(value),
                        "type": var.type,
                        "variablesReference": 0
                    })
        elif ref == 1002:
            # Stack
            for i, v in enumerate(step.stack):
                vars_list.append({"name": f"stack[{i}]", "value": hex(v) if isinstance(v, int) else str(v), "variablesReference": 0})
        self._response(request, True, {"variables": vars_list})

    def evaluate(self, request):
        expr = request.get("arguments", {}).get("expression", "")
        result = "n/a"
        try:
            if expr.startswith("stack[") and expr.endswith("]") and self.debugger and self.debugger.current_trace:
                idx = int(expr[6:-1])
                val = self.debugger.current_trace.steps[self.debugger.current_step].stack[idx]
                result = hex(val) if isinstance(val, int) else str(val)
            self._response(request, True, {"result": result, "variablesReference": 0})
        except Exception as e:
            self._response(request, False, message=str(e))



    def start_log_server(self, host="127.0.0.1", port=9000):
        self.log_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.log_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.log_sock.bind((host, port))
        self.log_sock.listen(1)
        threading.Thread(target=self._accept_log_client, daemon=True).start()

    def _accept_log_client(self):
        if self.log_sock:
            self.log_client, addr = self.log_sock.accept()

    def log(self, msg):
        try:
            if hasattr(self, "log_client") and self.log_client:
                self.log_client.sendall((msg + "\n").encode("utf-8"))
        except Exception:
            pass

    # ---- Server loop ----
    def run(self):
        while True:
            msg = self._read()
            if msg is None:
                break
            if msg.get("type") != "request":
                continue
            cmd = msg.get("command")
            if cmd == "initialize":
                self.initialize(msg)
            elif cmd == "launch":
                self.launch(msg)
            elif cmd == "setBreakpoints":
                self.setBreakpoints(msg)
            elif cmd == "configurationDone":
                self._response(msg, True, {})
            elif cmd == "threads":
                self.threads(msg)
            elif cmd == "continue":
                self.continue_(msg)
            elif cmd == "next":
                self.next(msg)
            elif cmd == "stepIn":
                self.stepIn(msg)
            elif cmd == "stepOut":
                self.stepOut(msg)
            elif cmd == "stackTrace":
                self.stackTrace(msg)
            elif cmd == "scopes":
                self.scopes(msg)
            elif cmd == "variables":
                self.variables(msg)
            elif cmd == "evaluate":
                self.evaluate(msg)
            elif cmd == "disconnect" or cmd == "terminate":
                self._response(msg, True, {})
                break
            else:
                self._response(msg, False, message=f"Unsupported command: {cmd}")

if __name__ == "__main__":
    server = WalnutDAPServer()
    server.start_log_server(port=9000)
    time.sleep(3)
    server.run()