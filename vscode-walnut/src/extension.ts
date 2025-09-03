import * as vscode from 'vscode';
import path from 'path';

export function activate(context: vscode.ExtensionContext) {

	// Register the walnut debug configuration provider
	context.subscriptions.push(
		vscode.debug.registerDebugConfigurationProvider('walnut', {
			resolveDebugConfiguration(folder, config) {
				console.log('[Walnut] Resolving walnut debug configuration:', config);
				config.type = 'walnut';
				config.request = config.request || 'launch';
				config.port = config.port || 4711;
				console.log('[Walnut] Resolved debug configuration:', config);
				return config;
			}
		})
	);

	const extensionPath = context.extensionPath;
    const sourceDir = path.dirname(extensionPath);
    
	// Register the walnut debug adapter descriptor factory
	context.subscriptions.push(
		vscode.debug.registerDebugAdapterDescriptorFactory('walnut', {
			createDebugAdapterDescriptor(session) {
				console.log('[Walnut] Creating walnut debug adapter for session:', session.configuration);
				
				// Get configuration settings
				const config = vscode.workspace.getConfiguration('walnut');
				const pythonPath = config.get<string>('pythonPath') || 'python3';
				const walnutPath = config.get<string>('walnutPath') || '';
				
				// If no walnut path is configured, try to find it
				let cwd = walnutPath;
				let pythonPathEnv = walnutPath ? `${walnutPath}/src` : '';
				
				if (!walnutPath) {
					// Auto-detect walnut-cli directory
					let detectedPath = null;
					
					// For extension development, the walnut-cli project is typically in the parent directory
					if (vscode.workspace.workspaceFolders) {
						const workspaceFolder = vscode.workspace.workspaceFolders[0];
						const folderPath = workspaceFolder.uri.fsPath;
						console.log(`[Walnut] Current workspace: ${folderPath}`);
						
						try {
							const fs = require('fs');
							const path = require('path');
							
							// Look for walnut-cli indicators
							const indicators = [
								'src/soldb/__init__.py',
								'src/soldb/main.py',
								'src/soldb/dap_server.py'
							];
							
							// If this is the extension workspace, check parent directory first
							if (folderPath.endsWith('/vscode-walnut')) {
								const parentPath = path.dirname(folderPath);
								console.log(`[Walnut] Extension workspace detected, checking parent: ${parentPath}`);
								
								const hasParentIndicators = indicators.some(indicator => {
									const fullPath = path.join(parentPath, indicator);
									const exists = fs.existsSync(fullPath);
									console.log(`[Walnut] Checking parent ${indicator}: ${exists}`);
									return exists;
								});
								
								if (hasParentIndicators) {
									detectedPath = parentPath;
									console.log(`[Walnut] Auto-detected walnut-cli in parent directory: ${detectedPath}`);
								}
							}
							
							// If not found in parent, check current workspace
							if (!detectedPath) {
								console.log(`[Walnut] Checking current workspace for walnut-cli indicators: ${folderPath}`);
								
								const hasIndicators = indicators.some(indicator => {
									const fullPath = path.join(folderPath, indicator);
									const exists = fs.existsSync(fullPath);
									console.log(`[Walnut] Checking ${indicator}: ${exists}`);
									return exists;
								});
								
								if (hasIndicators) {
									detectedPath = folderPath;
									console.log(`[Walnut] Auto-detected walnut-cli at: ${detectedPath}`);
								}
							}
						} catch (error) {
							console.log(`[Walnut] Error during auto-detection:`, error);
						}
					}
					
					if (detectedPath) {
						cwd = detectedPath;
						pythonPathEnv = `${detectedPath}/src`;
					} else {
						vscode.window.showWarningMessage(
							'Walnut: Could not auto-detect walnut-cli directory. Please set walnut.walnutPath in settings to the directory containing src/soldb/'
						);
						// Use workspace as fallback, but it probably won't work
						const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
						if (workspaceFolder) {
							cwd = workspaceFolder.uri.fsPath;
							pythonPathEnv = `${cwd}/src`;
							console.log(`[Walnut] Fallback to workspace folder: ${cwd}`);
						}
					}
				}

				console.log(`[Walnut] Debug adapter config:`, {
					pythonPath,
					cwd,
					pythonPathEnv
				});
		
				const executable = new vscode.DebugAdapterExecutable(
					pythonPath,
					['-m', 'soldb.dap_server'],
					{
						cwd: cwd,
						env: { 
							...process.env, 
							...(pythonPathEnv && { PYTHONPATH: pythonPathEnv })
						}
					}
				);
				
				console.log('[Walnut] Created debug adapter executable:', {
					command: pythonPath,
					args: ['-m', 'soldb.dap_server'],
					cwd: cwd
				});
				
				return executable;
			}
		})
	);

	// Register a command for testing
	context.subscriptions.push(
		vscode.commands.registerCommand('wn.walnut', () => {
			vscode.window.showInformationMessage('Walnut Debugger Activated!');
		})
	);

	// Register CodeLens provider for Solidity
	context.subscriptions.push(
		vscode.languages.registerCodeLensProvider({ language: 'solidity' }, new SolidityCodeLensProvider())
	);

	// Register the command that CodeLens will trigger
	context.subscriptions.push(
		vscode.commands.registerCommand('wn.runFunction', async (functionName: string, args_cnt: number) => {
			console.log(`[Walnut] Debug button clicked for function: ${functionName}, args_cnt: ${args_cnt}`);
			
			let args: string[] = [];
			if (args_cnt > 0) {
				const argsInput = await vscode.window.showInputBox({
					prompt: `Enter arguments for ${functionName} (comma separated)`
				});
				args = argsInput ? argsInput.split(',').map(s => s.trim()) : [];
				if (args.length !== args_cnt) {
					vscode.window.showErrorMessage(`Expected ${args_cnt} arguments, but got ${args.length}`);
					return;
				}
			}
			
			const debugConfig = {
				type: 'walnut',
				name: `Debug ${functionName}`,
				request: 'launch',
				contractFile: vscode.window.activeTextEditor?.document.uri.fsPath,
				port: 4711,
				function: functionName,
				functionArgs: args // pass arguments to launch config
			};
			
			console.log(`[Walnut] Starting debug session with config:`, debugConfig);
			
			try {
				const success = await vscode.debug.startDebugging(undefined, debugConfig);
				console.log(`[Walnut] Debug session start result:`, success);
			} catch (error) {
				console.error(`[Walnut] Failed to start debug session:`, error);
				vscode.window.showErrorMessage(`Failed to start debugging: ${error}`);
			}
		})
	);
}

export function deactivate() {}

class SolidityCodeLensProvider implements vscode.CodeLensProvider {
    onDidChangeCodeLenses?: vscode.Event<void>;

    provideCodeLenses(document: vscode.TextDocument, token: vscode.CancellationToken): vscode.CodeLens[] {
        const codeLenses: vscode.CodeLens[] = [];
        const regex = /function\s+(\w+)\s*\(([^)]*)\)/g;
        for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i);
            const match = regex.exec(line.text);
            if (match) {
                const functionName = match[1];
                const params = match[2].trim();
                const args_cnt = params ? (params.match(/,/g) || []).length + 1 : 0;
                const range = new vscode.Range(i, 0, i, line.text.length);
                codeLenses.push(new vscode.CodeLens(range, {
                    title: 'Debug',
                    command: 'wn.runFunction',
                    arguments: [functionName, args_cnt]
                }));
            }
            regex.lastIndex = 0; // Reset regex for next line
        }
        return codeLenses;
    }
}
