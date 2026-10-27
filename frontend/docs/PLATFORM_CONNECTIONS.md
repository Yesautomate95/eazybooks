# Platform Connections

This guide explains how to connect the accounting platforms shown in the EazyBooks workspace picker.

## Before You Start

1. Start the EazyBooks backend on port `8000`.
2. Start the frontend and sign in to your EazyBooks account.
3. Make sure the account has the required RBAC permission for the platform.
4. For OAuth platforms, confirm that the provider application has been configured with the correct client credentials and callback URL in the deployment environment.
5. For Tally, keep Tally Prime and the EazyBooks desktop bridge running on the same computer.

Open the connection manager at:

```text
Dashboard -> Settings -> Connections
```

You can also select a workspace first and return to **Settings -> Connections** at any time.

## Tally Prime

**Connection type:** Local desktop bridge

Tally Prime is connected through the EazyBooks desktop application. It does not use a cloud OAuth login.

### Steps

1. Install and open Tally Prime on the computer where the company data is available.
2. Open the required company in Tally Prime.
3. Start the EazyBooks desktop application or Electron bridge.
4. Sign in to EazyBooks with the same account used by the desktop bridge.
5. In EazyBooks, open **Settings -> Connections**.
6. Confirm that Tally shows as connected.
7. Open the Tally workspace and select the company to sync.

The desktop bridge must remain connected while using Tally ledgers, inventory, vouchers, bank entries, or sync operations.

### Troubleshooting

- Confirm that Tally Prime is open and a company is loaded.
- Confirm that the desktop bridge is signed in with the expected EazyBooks account.
- Check that the bridge can reach the backend URL.
- Confirm the local Tally service URL configured for the backend. The backend setting is `TALLY_URL`; the default is `http://localhost:9000`.
- Use **Re-sync** after reconnecting the bridge.

## QuickBooks Online

**Connection type:** OAuth 2.0

### Steps

1. Open **Settings -> Connections**.
2. Select **Connect** beside **QuickBooks Online**.
3. Sign in to Intuit in the provider window.
4. Select the QuickBooks company when prompted.
5. Approve the requested permissions.
6. Return to EazyBooks.
7. Confirm the status changes to **Connected**.
8. Open the QuickBooks workspace and run a sync if data is not immediately visible.

The frontend uses the OAuth connection identifier `quickbooks` and the callback marker `qbo`.

### Required provider setup

The backend deployment must have a registered Intuit application with:

- OAuth 2.0 client ID
- OAuth 2.0 client secret
- Redirect URI matching the deployed backend callback route
- Requested QuickBooks scopes
- Secure token storage

Never put the client secret in frontend code or commit it to the repository.

## Zoho Books

**Connection type:** OAuth 2.0

### Steps

1. Open **Settings -> Connections**.
2. Select **Connect** beside **Zoho Books**.
3. Sign in to Zoho.
4. Select the organization to connect.
5. Approve the requested permissions.
6. Return to EazyBooks and confirm the connected status.
7. Open the Zoho workspace and sync the organization data.

The frontend uses the OAuth connection identifier `zoho` and callback marker `zoho`.

### Required provider setup

The backend deployment must have a registered Zoho application with:

- OAuth client ID
- OAuth client secret
- Correct Zoho data-center authorization and token URLs
- Redirect URI matching the deployed backend callback route
- Organization and accounting scopes
- Secure refresh-token storage

## Xero

**Connection type:** OAuth 2.0

### Steps

1. Open **Settings -> Connections**.
2. Select **Connect** beside **Xero**.
3. Sign in to Xero.
4. Select the Xero organization.
5. Approve the requested permissions.
6. Return to EazyBooks and confirm the connected status.
7. Open the Xero workspace and sync the organization data.

The frontend uses the OAuth connection identifier `xero` and callback marker `xero`.

### Required provider setup

The backend deployment must have a registered Xero application with:

- OAuth client ID
- OAuth client secret
- Redirect URI matching the deployed backend callback route
- Required accounting scopes
- Secure refresh-token storage

## SAP S/4HANA

**Connection type:** OAuth 2.0

### Steps

1. Open **Settings -> Connections**.
2. Select **Connect** beside **SAP S/4HANA**.
3. Sign in through the configured SAP identity provider.
4. Select or confirm the SAP tenant and company context.
5. Approve the requested permissions.
6. Return to EazyBooks and confirm the connected status.
7. Open the SAP workspace and start synchronization.

The frontend uses the OAuth connection identifier `sap` and callback marker `sap`.

### Required provider setup

The backend deployment must have a configured SAP integration with:

- SAP tenant or base URL
- OAuth client ID and client secret
- Identity-provider authorization and token endpoints
- Redirect URI matching the deployed backend callback route
- Required SAP accounting API scopes
- Secure storage for access and refresh tokens

## Connection Statuses

The Connections page can show these states:

| Status | Meaning |
|---|---|
| `Disconnected` | No active connection is available. |
| `Connecting` | Authentication or desktop-bridge negotiation is in progress. |
| `Connected` | The platform is available to the account. |
| `Syncing` | Data import is currently running. |
| `Failed` | The last connection or sync attempt failed. Use **Retry** or review the error. |

## Re-sync and Disconnect

- Select **Re-sync** for a connected platform to import the latest available data.
- Do not close the desktop bridge during a Tally sync.
- Select **Disconnect** to remove the platform connection. EazyBooks asks for confirmation before disconnecting.
- Disconnecting should revoke or remove provider tokens on the backend. It should not expose tokens in the browser, logs, API responses, or Admin UI.

## Permissions

Platform access is controlled by the backend RBAC system. The relevant workspace permissions are:

| Platform | Permission |
|---|---|
| Tally Prime | `tally.read` |
| QuickBooks Online | `quickbooks.read` |
| Zoho Books | `zoho.read` |
| Xero | `xero.read` |
| SAP S/4HANA | `sap.read` |

A user without the required permission may not see or enter that workspace. Frontend visibility is only a usability feature; the backend must enforce authorization on every protected API endpoint.

## Security Rules

- Use HTTPS outside local development.
- Store OAuth client secrets and refresh tokens only on the backend or a managed secret store.
- Do not paste access tokens into support tickets or documentation.
- Do not add provider credentials to React environment variables unless the provider explicitly documents them as public values.
- Use separate OAuth applications and callback URLs for development and production.
- After changing frontend environment variables, restart the React development server.

## Current Integration Boundary

The workspace picker and Connections page define the connection modes and platform identifiers. Tally has backend connector routes and a desktop bridge. OAuth platforms require their provider-specific backend OAuth callback, token storage, refresh, disconnect, and data-sync implementation to be enabled in the deployment. A platform card appearing in the UI does not by itself establish a live provider connection.
