---
name: troubleshoot-functions-startup
description: "Diagnose and resolve Azure Functions host startup failures — the 'Function host is not running' errors, 503s, missing functions, and restart loops. Walks through a systematic checklist covering app settings, storage connectivity, host.json, extension bundles, deployment packages, startup code, worker runtime, networking, and platform issues. WHEN: function host is not running, functions not starting, Azure Functions 503, functions disappeared, function app error state, functions restart loop, host startup issue, FUNCTIONS_EXTENSION_VERSION error, FUNCTIONS_WORKER_RUNTIME mismatch, AzureWebJobsStorage error, host.json version missing, extension bundle error, run from package failed, function app down, functions not visible in portal, triggers not firing, timer trigger silent, queue trigger not working, function app crash, AZFD0005, AZFD0006, AZFD0009, AZFD0011, AZFD0013, function app offline, app_offline.htm, gRPC worker failure, function app VNet startup failure, function app deployment broke functions."
---

# Troubleshoot Azure Functions Host Startup Issues

When the Azure Functions host fails to start, functions become invisible, triggers stop firing, and HTTP endpoints return 503s. This skill provides a systematic approach to diagnosing and fixing these failures.

## Step 0: Identify the Function App

Before you can investigate, you need to know which Function App to look at. If the user hasn't provided this information, ask them for it using the ask_user tool:

- **Function App name** (required)
- **Resource group** (helpful but can be discovered)
- **Subscription** (helpful but can be discovered)

If only the name is provided, use `azure-mcp-functionapp` with command `functionapp_get` to search across the subscription and locate the resource group automatically. If multiple matches come back, ask the user to clarify.

## Step 1: Investigate with Azure MCP Tools

Once you have the Function App identity, run these checks in order using the Azure MCP tools. Perform the first three calls in parallel to save time — they're independent reads.

### 1a. Get Function App Details

Use `azure-mcp-functionapp` → `functionapp_get` with the app name and resource group. This reveals the app's current state (Running/Stopped), hosting plan (Consumption/Premium/Dedicated), OS, runtime stack, and VNet integration status. These details are critical context for narrowing down the failure category.

### 1b. Read Application Settings

Use `azure-mcp-appservice` → `appservice_webapp_settings_get-appsettings` with the app name and resource group. Immediately check these critical settings:

| Setting | What to verify |
|---------|---------------|
| `FUNCTIONS_EXTENSION_VERSION` | Must be `~4` (or `~3` for legacy). Missing or invalid → immediate startup failure |
| `FUNCTIONS_WORKER_RUNTIME` | Must match deployed code: `dotnet`, `dotnet-isolated`, `node`, `python`, `java`, `powershell` |
| `AzureWebJobsStorage` | Must contain a valid connection string. Redacted values are OK (MCP masks secrets) — focus on whether it exists |
| `WEBSITE_CONTENTAZUREFILECONNECTIONSTRING` | Required on Consumption/Premium plans |
| `WEBSITE_CONTENTSHARE` | Required on Consumption/Premium plans |
| `WEBSITE_RUN_FROM_PACKAGE` | If set, note the value (`1` or a URL) for package investigation |

Flag any missing or suspicious values immediately.

### 1c. Check Resource Health

Use `azure-mcp-resourcehealth` → `resourcehealth_availability-status_get` with the Function App's full resource ID (constructed as `/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Web/sites/{appName}`). This reveals whether Azure itself considers the resource healthy, unavailable, or degraded, and can surface platform-level issues that no amount of config checking will find.

### 1d. Run AppLens Diagnostics

Use `azure-mcp-applens` → `applens_resource_diagnose` with:
- `resource`: the Function App name
- `question`: "Function App host startup failure — why is the host not reaching Running state?"
- `resource-group` and `subscription` if available

AppLens runs the same automated detectors that power the portal's "Diagnose and Solve Problems" blade. It often identifies the root cause faster than manual investigation and provides targeted recommendations.

### 1e. Check App Service Diagnostics Detectors

Use `azure-mcp-appservice` → `appservice_webapp_diagnostic_diagnose` with the app name, resource group, and these detector names (run whichever are relevant based on findings so far):

| Detector Name | When to Use |
|--------------|-------------|
| `FunctionAppDown` | Always — checks overall health and crash history |
| `Availability` | If 503s or timeouts are reported |
| `CpuAnalysis` | If resource exhaustion is suspected |
| `MemoryAnalysis` | If OOM or memory pressure signs |

### 1f. Check Recent Deployments

Use `azure-mcp-appservice` → `appservice_webapp_deployment_get` with the app name and resource group. If the issue started after a deployment, this reveals the deployment type, timing, and whether it completed successfully. Look for failed deployments or deployments that coincide with the start of the issue.

### 1g. Query Application Insights Logs

If the Function App has Application Insights configured (check for `APPINSIGHTS_INSTRUMENTATIONKEY` or `APPLICATIONINSIGHTS_CONNECTION_STRING` in the app settings), use `azure-mcp-monitor` to query for startup exceptions:

Use command `monitor_query_logs-resource` with the Function App's resource ID and a KQL query like:

```kusto
AppExceptions
| where TimeGenerated > ago(1h)
| order by TimeGenerated asc
| take 20
```

The **first** exception after the most recent restart is the root cause — everything after is cascade. Also try:

```kusto
AppTraces
| where TimeGenerated > ago(1h)
| where Message contains "AZFD" or Message contains "HostInitializationException" or Message contains "startup"
| order by TimeGenerated asc
| take 20
```

This surfaces the Azure Functions diagnostic codes (AZFD0005, AZFD0009, AZFD0011, AZFD0013) that pinpoint specific configuration problems.

### 1h. Check Service Health Events

Use `azure-mcp-resourcehealth` → `resourcehealth_health-events_list` to check for any active Azure service incidents affecting the subscription. Platform outages can cause startup failures that have nothing to do with the app's configuration.

## Step 2: Apply a Fix Using Azure MCP

Once the root cause is identified, use `azure-mcp-appservice` → `appservice_webapp_settings_update-appsettings` to fix misconfigured app settings directly. For example:

- Set a missing `FUNCTIONS_EXTENSION_VERSION` to `~4`
- Correct a wrong `FUNCTIONS_WORKER_RUNTIME` value
- Update a stale `AzureWebJobsStorage` connection string

Always confirm with the user before making changes — these are production settings. After applying a fix, tell the user to restart the Function App from the portal or CLI, then re-run the health checks above to verify recovery.

## Understanding the Problem

The sections below explain each failure category in detail — what causes it, how to recognize it, and how to fix it. Use these as reference material once the MCP investigation has narrowed down the issue.

## Recognizing a Host Startup Failure

Look for these symptoms — any combination signals a startup issue:

- **"Function host is not running"** in the Azure Portal
- Functions missing from the Functions blade
- HTTP functions returning **503**, timer/queue functions going silent
- Portal showing **Error** state or no response on the host status endpoint
- Application Insights logs showing repeated startup exceptions followed by restarts
- Log Stream showing a restart loop or no output

## The Startup Sequence

Understanding what the host does at startup helps pinpoint where it failed:

```
ASP.NET Core Startup
  → Register WebHost services (DI, secrets, diagnostics, middleware)
    → WebJobsScriptHostService.StartAsync()
      → Check file system (run-from-package validation)
        → Build inner ScriptHost
          → ScriptHost.InitializeAsync()
            → PreInitialize (validate settings, file system)
            → Load function metadata (function.json / decorators)
            → Load extensions and bindings (extension bundles / NuGet)
            → Create function descriptors and register triggers
              → Start trigger listeners
                → State = Running ✓
```

If any step fails, the host enters **Error** state and retries with exponential backoff (1s → up to 2min between attempts).

## Host States

| State | Meaning |
|-------|---------|
| **Default** | Host not yet created |
| **Starting** | Host is initializing |
| **Initialized** | Functions indexed, listeners not yet running |
| **Running** | Fully running — triggers active, functions discoverable |
| **Error** | Startup failed — will attempt restart |
| **Stopping** | Host shutting down |
| **Stopped** | Host stopped |
| **Offline** | `app_offline.htm` is present |

Only **Running** means everything is healthy.

## Diagnostic Checklist

Work through this checklist in order. Use the Azure MCP tools from Step 1 above for automated investigation, then cross-reference against these items:

1. **Function App details** — use `functionapp_get` to check state, plan, and VNet integration
2. **App settings** — use `appservice_webapp_settings_get-appsettings` to validate all critical settings
3. **Resource health** — use `resourcehealth_availability-status_get` to check platform health
4. **AppLens diagnostics** — use `applens_resource_diagnose` for automated root cause analysis
5. **First error** — query Application Insights with `monitor_query_logs-resource` for the first exception after latest restart
6. **FUNCTIONS_EXTENSION_VERSION** — is it set to a valid value (e.g., `~4`)?
7. **FUNCTIONS_WORKER_RUNTIME** — does it match the deployed code?
8. **AzureWebJobsStorage** — is the connection string valid and the storage account reachable?
9. **host.json** — does it exist, contain valid JSON, and include `"version": "2.0"`?
10. **Extension bundle** — is `extensionBundle` configured with a compatible version range?
11. **Package deployment** — if using `WEBSITE_RUN_FROM_PACKAGE`, is the package accessible and correctly structured?
12. **Startup code** — for .NET apps, does `Program.cs` or startup code throw during DI registration?
13. **Networking** — if VNet-integrated, can the app reach storage, Key Vault, and extension CDN?
14. **Offline file** — is `app_offline.htm` present in the root directory?
15. **Recent deployments** — use `appservice_webapp_deployment_get` to check for failed deployments
16. **Service health** — use `resourcehealth_health-events_list` to check for Azure platform incidents

## Checking Host Status via REST API (Manual Fallback)

If the Azure MCP tools are unavailable or you need to check the host status directly, use the admin API:

```bash
curl "https://<app>.azurewebsites.net/admin/host/status?code=<master-key>"
```

The `state` field tells you what's happening:

| State | Action |
|-------|--------|
| Running | Host is healthy — investigate function-level issues instead |
| Error | Host startup failed — check the `errors` array for root cause |
| Offline | `app_offline.htm` present — check deployment state |
| No response / timeout | Host cannot serve requests — check platform health and networking |

To verify function discovery:
```bash
curl "https://<app>.azurewebsites.net/admin/functions?code=<master-key>"
```

## Issue: Invalid or Missing FUNCTIONS_EXTENSION_VERSION

**Symptoms:** Host fails immediately. Error: _"Invalid site extension configuration. Please update the App Setting 'FUNCTIONS_EXTENSION_VERSION' to a valid value (e.g. ~4)."_

**Why:** This setting tells the platform which runtime version to load. It's validated in `ScriptHost.PreInitialize()`. Missing or unrecognized values cause a `HostInitializationException`.

**Verify:** Portal → Settings → Configuration → Application settings. Confirm the value is `~4` (recommended), `~3` (legacy), or a specific version.

**Fix:** Set `FUNCTIONS_EXTENSION_VERSION` to `~4` (or appropriate version). Save and restart.

**Ref:** [FUNCTIONS_EXTENSION_VERSION](https://learn.microsoft.com/en-us/azure/azure-functions/functions-app-settings#functions_extension_version)

## Issue: Wrong or Missing FUNCTIONS_WORKER_RUNTIME

**Symptoms:** Error: _"The 'FUNCTIONS_WORKER_RUNTIME' setting is required..."_ (AZFD0011) or _"...does not match the worker runtime metadata..."_ (AZFD0013). Host enters Error after loading function metadata.

**Why:** This setting controls which language worker the host launches. A mismatch between the setting and the deployed code causes a `HostInitializationException`.

**Verify:** Check the app setting matches your project type:

| Project Type | Correct Value |
|-------------|---------------|
| C# in-process | `dotnet` |
| C# isolated | `dotnet-isolated` |
| Node.js | `node` |
| Python | `python` |
| Java | `java` |
| PowerShell | `powershell` |

**Fix:** Set the correct value. If you migrated (e.g., in-process → isolated), update accordingly. Save and restart.

**Ref:** [FUNCTIONS_WORKER_RUNTIME](https://learn.microsoft.com/en-us/azure/azure-functions/functions-app-settings#functions_worker_runtime)

## Issue: AzureWebJobsStorage Unreachable or Invalid

**Symptoms:** Host fails repeatedly with Blob storage connectivity errors, "Unable to get function keys", secret management errors, or health check returning Unhealthy.

**Why:** The Functions host needs storage for keys/secrets, coordinating distributed triggers, internal state/locks, and hosting the content share (Consumption/Premium plans). A background health check runs every 30 seconds. If storage is unreachable (wrong connection string, rotated keys, firewall, deleted account, expired SAS), the host cannot initialize.

**Verify these settings:**

| Setting | Required For |
|---------|-------------|
| `AzureWebJobsStorage` | All plans — primary storage connection |
| `WEBSITE_CONTENTAZUREFILECONNECTIONSTRING` | Consumption/Premium — content share |
| `WEBSITE_CONTENTSHARE` | Consumption/Premium — file share name |

**Fix:**
1. Verify the storage account exists and is not deleted/disabled
2. If keys were rotated, update the connection string from Storage Account → Access keys
3. Check storage firewall: Storage Account → Networking — ensure the Function App has access
4. For SAS-token connections, verify the token hasn't expired (AZFD0006)
5. For VNet-integrated apps, ensure service/private endpoints are configured and DNS resolves for `*.blob.core.windows.net`, `*.queue.core.windows.net`, `*.table.core.windows.net`, `*.file.core.windows.net`

**Ref:** [Storage considerations](https://learn.microsoft.com/azure/azure-functions/storage-considerations)

## Issue: Invalid host.json

**Symptoms:** Error: _"The host.json file is missing the required 'version' property."_ (AZFD0009), JSON deserialization failures, or host entering `HandlingConfigurationParsingError` mode.

**Why:** `host.json` is parsed early in startup. Missing `"version": "2.0"`, invalid JSON, or unrecognized config values cause a `HostConfigurationException`. The host restarts in a degraded mode where admin APIs work but functions don't load.

**Verify:** Check `host.json` via Kudu (Windows: `site/wwwroot/host.json`) or SSH (Linux). Confirm it's valid JSON with `"version": "2.0"`.

**Minimal valid host.json:**
```json
{
  "version": "2.0"
}
```

**Typical host.json with extension bundle:**
```json
{
  "version": "2.0",
  "extensionBundle": {
    "id": "Microsoft.Azure.Functions.ExtensionBundle",
    "version": "[4.*, 5.0.0)"
  },
  "logging": {
    "applicationInsights": {
      "samplingSettings": {
        "isEnabled": true,
        "excludedTypes": "Request"
      }
    }
  }
}
```

**Fix:** Fix JSON syntax, add `"version": "2.0"`, remove unrecognized keys. Redeploy or edit via Kudu.

**Ref:** [host.json reference](https://learn.microsoft.com/en-us/azure/azure-functions/functions-host-json)

## Issue: Extension Bundle or Binding Load Failure

**Symptoms:** Extension-related errors at startup, _"Referenced bundle X of version Y does not meet the required minimum version..."_, errors referencing `ScriptStartUpErrorLoadingExtensionBundle` or `ScriptStartUpUnableToLoadExtension`. Works locally but fails in Azure.

**Why:** During startup, the host loads trigger/binding implementations from extension bundles or the bin folder. Missing bundles, incompatible versions, assembly load failures, or type mismatches cause a `HostInitializationException`.

**Verify:**
1. Check `host.json` for `extensionBundle` configuration
2. Verify the version range is compatible with your runtime
3. For compiled C# apps without bundles, verify NuGet packages are present and compatible

**Fix:** Ensure `extensionBundle` is configured correctly:
```json
{
  "version": "2.0",
  "extensionBundle": {
    "id": "Microsoft.Azure.Functions.ExtensionBundle",
    "version": "[4.*, 5.0.0)"
  }
}
```

For compiled .NET apps: update extension NuGet packages, ensure `extensions.json` is in the bin folder, and check for assembly version conflicts.

## Issue: Run-From-Package Failure

**Symptoms:** Host shuts down immediately, error: _"Shutting down host due to presence of FAILED TO INITIALIZE RUN FROM PACKAGE.txt"_, functions that were visible before disappear, "No functions found", read-only file system errors.

**Why:** When `WEBSITE_RUN_FROM_PACKAGE` is set, the host runs from a ZIP package. If `FAILED TO INITIALIZE RUN FROM PACKAGE.txt` is found, the host **immediately shuts down** — this is fatal and requires redeployment.

**WEBSITE_RUN_FROM_PACKAGE values:**

| Value | Behavior |
|-------|----------|
| `1` | Runs from local package in `d:\home\data\SitePackages` (Windows) or `/home/data/SitePackages` (Linux) |
| `<URL>` | Runs from a remote package at the URL (required for Linux Consumption) |
| Not set | Traditional deployment — files extracted to wwwroot |

**Verify:**
1. Check the setting value in Application Settings
2. If `1`: Kudu → `d:\home\data\SitePackages` → verify ZIP exists and `packagename.txt` points to it
3. If URL: try accessing it directly — should download the ZIP. Check for 403 (expired SAS) or 404 (missing blob)
4. Download and extract the ZIP — confirm `host.json` and function files are at the **root level**, not nested in a subfolder

**Common problems:**

| Problem | Symptom | Fix |
|---------|---------|-----|
| Expired SAS token | URL returns 403 | Generate new SAS with longer expiry |
| Missing blob | URL returns 404 | Verify blob exists and URL is correct |
| Wrong package structure | Files in subfolder | Ensure files are at ZIP root |
| Corrupted package | Startup errors | Redeploy with fresh package |
| Storage firewall | Timeout errors | Allow Function App access to storage |

**Fix:** Redeploy. For URL-based packages, regenerate the SAS token or use managed identity. Restart after fixing.

**Ref:** [Run from package](https://learn.microsoft.com/en-us/azure/azure-functions/run-functions-from-deployment-package)

## Issue: Startup Code / DI Failure

**Symptoms:** Error state with application-specific exceptions, _"Error configuring services in an external startup class"_ (AZFD0005), DI failures (`InvalidOperationException`, `TypeLoadException`), errors in `Program.cs` or `Startup.cs`, assembly binding conflicts.

**Why:** For isolated worker (.NET) apps, `Program.cs` runs custom startup code before the worker connects. For in-process apps, `IWebJobsStartup` implementations run during host init. If this code throws (missing dependency, failed service connection, type load error), the host enters Error state.

**Verify:**
1. Check Application Insights **Exceptions** table for the specific exception and stack trace
2. Look for AZFD0005 errors
3. Review `Program.cs` / `Startup.cs` for service registrations depending on external resources, missing NuGet packages, or config values that differ between local and Azure

**Fix:**
1. Fix the exception identified in logs — the stack trace usually points directly to the failing code
2. Ensure all required environment variables and connection strings are set in Application Settings
3. Resolve assembly conflicts by aligning NuGet package versions
4. Make external-service connections resilient (defer initialization, add retry logic)
5. Test startup locally with the same environment variables as Azure

## Issue: Language Worker / gRPC Failure

**Symptoms:** Error: _"Failed to start Language Worker Channel for language: {runtime}"_, _"Failed to start Rpc Server..."_, host starts but can't communicate with the worker, timeout errors during worker init.

**Why:** For out-of-process languages (Node.js, Python, Java, PowerShell, .NET Isolated), the host communicates with a separate worker over gRPC. Port conflicts, missing/wrong language runtime versions, worker crashes, or resource exhaustion prevent the connection.

**Verify:**
1. Check Application Insights for gRPC or worker errors
2. Verify language runtime version:
   - Node.js: `WEBSITE_NODE_DEFAULT_VERSION`
   - Python: Configuration → General settings
   - Java: `FUNCTIONS_WORKER_JAVA_LOAD_APP_LIBS` and Java version
   - .NET Isolated: target framework in deployed assemblies
3. Check plan resource limits

**Fix:** Set the correct language runtime version, verify the runtime stack (Linux Consumption), scale up if resource-limited, restart to clear temporary port/resource issues.

## Issue: Networking Blocking Startup (VNet)

**Symptoms:** Host fails in VNet-integrated apps, timeout errors connecting to storage or Azure services, works without VNet but fails with it, DNS resolution failures, NSG/firewall errors.

**Why:** During startup the host must reach:
- **Azure Storage** (Blob, Queue, Table, File) — keys, triggers, state
- **Extension bundle CDN** — download extensions on cold start
- **Azure Key Vault** — if Key Vault references are used
- **Application Insights** — telemetry (non-blocking but can delay)

VNet integration, NSG rules, forced tunneling, or firewalls can block these.

**Verify:**
1. Check VNet integration in the Networking blade
2. Review NSG rules on the integrated subnet — outbound to Azure services must be allowed
3. For forced tunneling, verify the firewall/NVA allows required endpoints
4. Check DNS resolution for storage endpoints from VNet context

**Fix:**
1. Add NSG/firewall rules for required outbound endpoints
2. Configure service/private endpoints for storage on the integrated subnet
3. Ensure DNS resolution works for all required endpoints
4. For private DNS zones, ensure proper zone links and records

**Ref:** [Networking options](https://learn.microsoft.com/azure/azure-functions/functions-networking-options)

## Issue: app_offline.htm Present

**Symptoms:** Host status shows Offline, all requests return an offline page, portal shows the app running but functions return errors.

**Why:** If `app_offline.htm` exists in the script root, the host enters Offline state. Some deployment tools create this file during deployment and should remove it afterward. A failed deployment can leave it behind.

**Verify:** Check for the file:
- Windows: Kudu → Debug Console → `site/wwwroot/app_offline.htm`
- Linux: SSH or Azure CLI

**Fix:** Delete `app_offline.htm`. The host detects deletion automatically and restarts normally. If it reappears, investigate your deployment pipeline.

## Azure Portal Diagnostics

The same detectors available in the portal can be accessed programmatically via Azure MCP tools (see Step 1d and 1e above). If you prefer the portal UI instead:

1. Navigate to the Function App → **Diagnose and solve problems**
2. Search for relevant detectors:

| Detector | What It Checks |
|----------|---------------|
| **Function App Down or Reporting Errors** | Overall health, host status, crash history |
| **Function App Startup Issue** | Startup failure analysis, config validation |
| **Functions Configurations Check** | host.json and app settings validation |
| **Functions Deployment** | Recent deployment status and issues |
| **Network Troubleshooter** | VNet, private endpoint, access restriction diagnostics |

## Diagnostic Codes Reference

These codes appear in Application Insights traces and diagnostic event logs:

| Code | Name | Meaning |
|------|------|---------|
| AZFD0005 | External Startup Error | Error in a custom `IWebJobsStartup` class |
| AZFD0006 | SAS Token Expiring | `AzureWebJobsStorage` SAS token is expiring or expired |
| AZFD0009 | Unable to Parse host.json | host.json missing or has invalid content |
| AZFD0011 | Missing FUNCTIONS_WORKER_RUNTIME | Required worker runtime setting not configured |
| AZFD0013 | Worker Runtime Mismatch | Setting doesn't match deployed function metadata |

**Ref:** [Diagnostic Events](https://learn.microsoft.com/en-us/azure/azure-functions/errors-diagnostics/diagnostic-events/azfd0005)

## Key Principles

1. **Always check host status first** — `/admin/host/status` gives you state + errors
2. **Find the first error, not the cascade** — look for the initial exception after the most recent restart
3. **Validate the big three settings** — `FUNCTIONS_EXTENSION_VERSION`, `FUNCTIONS_WORKER_RUNTIME`, and `AzureWebJobsStorage` cause the most startup failures
4. **Check host.json** — missing `"version"` or invalid JSON is common and easy to fix
5. **Verify deployment artifacts** — package must be complete, correctly structured, and accessible
6. **Use built-in diagnostics** — the Diagnose and Solve Problems detectors are purpose-built for this
7. **Apply one fix at a time** — change one setting, restart, recheck. Avoid simultaneous changes

## Escalation

If the issue persists after following this checklist, open a support ticket with:
- Function App name and resource group
- Timestamp of when the issue started
- Host status endpoint response (full JSON)
- The first exception from Application Insights or Log Stream
- Recent deployment or configuration changes
- Networking configuration details (if VNet-integrated)

## References

- [host.json reference](https://learn.microsoft.com/azure/azure-functions/functions-host-json)
- [App settings reference](https://learn.microsoft.com/azure/azure-functions/functions-app-settings)
- [Deployment technologies](https://learn.microsoft.com/azure/azure-functions/functions-deployment-technologies)
- [Storage considerations](https://learn.microsoft.com/azure/azure-functions/storage-considerations)
- [Networking options](https://learn.microsoft.com/azure/azure-functions/functions-networking-options)
- [Azure Functions diagnostics](https://learn.microsoft.com/azure/azure-functions/functions-diagnostics)
- [Admin API (host status)](https://github.com/Azure/azure-functions-host/wiki/Admin-API)
- [Run from package](https://learn.microsoft.com/azure/azure-functions/run-functions-from-deployment-package)
- [Troubleshoot Azure Functions](https://learn.microsoft.com/azure/azure-functions/functions-recover-storage-account)
