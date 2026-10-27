# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A React SPA for EazyBooks - an AI-powered Chartered Accountant platform with Tally ERP integration, document management, and financial dashboards.

## Tech Stack

- **Framework**: React 18.2 with Create React App
- **Routing**: React Router DOM 7.0
- **UI Libraries**: Material-UI 7.1, Radix UI, Tailwind CSS 3.4
- **State Management**: React Context (EazyBooksContext)
- **HTTP Client**: Axios with interceptors
- **Authentication**: Firebase 11.1 (Google OAuth, Email/Password)
- **Charts**: Recharts, AG Grid, MUI X Data Grid Pro
- **Styling**: Tailwind CSS, Emotion, Framer Motion
- **Document Processing**: jsPDF, XLSX, React PDF, React Markdown

## Project Structure

```
├── public/                    # Static assets, index.html, redirects
├── src/
│   ├── index.js              # React entry point
│   ├── App.js                # Root component with routing
│   ├── index.css             # Global styles with Tailwind
│   │
│   ├── services/             # Centralized API layer
│   │   ├── api.js            # Axios instance + all API modules
│   │   └── auth.js           # Token management
│   │
│   ├── components/
│   │   ├── context/
│   │   │   └── EazyBooksContext.js   # Global auth state provider
│   │   ├── auth/
│   │   │   ├── firebase-config.js # Firebase initialization
│   │   │   ├── firebase-auth.js   # Firebase auth logic
│   │   │   └── ProtectedRoute.jsx # Route protection wrapper
│   │   ├── ui/               # Reusable UI components
│   │   │   ├── button.jsx
│   │   │   └── dialog.jsx
│   │   └── Pages/            # Page components
│   │       ├── Landing/
│   │       ├── Auth/
│   │       ├── ChatBot/
│   │       └── EazyBooksCA/     # Main dashboard pages
│   │
│   ├── lib/utils.js          # Utility functions (cn for classnames)
│   ├── data/ChatPrompts.js   # Predefined chat prompts
│   └── assets/               # Images, logos, Lottie animations
│
├── electron_app/             # Desktop app (Electron)
│   ├── package.json          # Electron build config
│   └── src/main/electron.js  # Main process
│
├── tailwind.config.js        # Tailwind configuration
├── .env                      # Environment variables
└── package.json              # Dependencies and scripts
```

## Key Commands

```bash
# Start development server
npm start

# Build for production
npm run build

# Run tests
npm test

# Install dependencies
npm install
```

## Routes

```
/                    → LandingPage
/login               → Login
/signup              → Signup
/dashboard/*         → EazyBooksCA (Protected)
  ├── /chat          → ChatbotLight
  ├── /lockr         → Document locker
  ├── /clients       → Client management
  ├── /history       → Chat history
  ├── /notice-aid    → Notice assistance
  ├── /cloud         → Cloud storage
  ├── /tally         → Tally integration
  ├── /tally-chat    → Tally chatbot
  └── /mis           → Management Info System
```

## Environment Variables

```
REACT_APP_API_URL=https://api.aureusfin.in   # Backend API URL
GENERATE_SOURCEMAP=false                      # Disable source maps in prod
SKIP_PREFLIGHT_CHECK=true                     # Skip CRA preflight
```

## API Services (src/services/api.js)

Centralized Axios instance with:
- Auto token attachment via request interceptor
- 401 error handling with automatic token refresh
- Token refresh every 5 minutes

API modules:
- `authApi` - Authentication (Google sign-in, email auth)
- `chatApi` - LangGraph streaming chat, history
- `tallyApi` - Company management, inventory, ledger operations
- `filesApi` - Upload, delete, search documents
- `clientsApi` - Client CRUD operations
- `misApi` - Financial summary, trends, GST reports
- `historyApi` - Chat history management

## State Management

- **Global**: `EazyBooksContext` for authentication state
- **Local**: useState hooks within components
- No Redux - context-based approach

## Authentication Flow

1. Firebase handles Google OAuth or email/password
2. JWT tokens stored in localStorage
3. Auto-refresh every 5 minutes
4. ProtectedRoute wrapper checks auth status
5. 401 responses trigger token refresh

## Key Features

### Chat System
- Streaming responses via Fetch API (SSE)
- Multi-file upload with preview
- Markdown rendering with math support (KaTeX)
- Predefined prompts in `data/ChatPrompts.js`

### Tally Integration Architecture

**Critical: Two connection methods exist with different use cases:**

1. **Direct HTTP** (`/ca/tally/*` endpoints like `/ca/tally/status`, `/ca/push-vouchers-batch`)
   - Backend connects directly to Tally at localhost:9000
   - Only works when backend server can reach user's local Tally
   - Works in local development, **fails in production**

2. **WebSocket via Desktop App** (`/ca/push-to-tally`, `/ca/check-tally-connection`)
   - Uses Electron desktop app as bridge to local Tally
   - Desktop app runs locally, receives commands via websocket, forwards to Tally
   - **Required for production** since cloud backend cannot reach local Tally

**Correct push pattern for production:**
```javascript
// Step 1: Generate XML on backend
const xmlResponse = await tallyApi.generateBankXml({ company, bank_ledger, transactions });
// Step 2: Push via websocket (works through desktop app)
const pushResponse = await tallyApi.pushToTally({ company, xml: xmlResponse.xml });
```

**Avoid in production:**
```javascript
// This fails - direct HTTP from cloud to local Tally
await tallyApi.pushVouchersBatch({ ... });  // Uses /ca/push-vouchers-batch
await tallyApi.validatePrePush({ ... });     // Uses direct connection
```

**Connection checking pattern:**
```javascript
// Try direct first, fallback to websocket
let isConnected = false;
try {
  const result = await tallyApi.checkDirectConnection();
  isConnected = result?.connected ?? false;
} catch {
  const wsResult = await tallyApi.checkConnection();
  isConnected = wsResult?.connected ?? false;
}
```

### Document Management (Lockr)
- File upload to S3
- PDF and image viewing
- Vector search across documents

### MIS Dashboards
- Financial summary and trends
- Cost center analysis
- Inventory reports
- GST summary

## Styling Conventions

- Tailwind CSS for utility classes
- Custom theme colors: `customBlack`, `customGreen`, `customRed`, `customPurple`
- CSS variables for theming (background, foreground, primary, muted)
- Framer Motion for animations
- `cn()` utility from `lib/utils.js` for conditional classes

## Desktop App (Electron)

Located in `electron_app/`:
- Windows NSIS installer
- Protocol handler: `aureus-ca://`
- Auto-update via electron-updater
- Version: 1.0.3

## Deployment

- Web: Static hosting (Netlify-compatible with `_redirects`)
- Desktop: Electron Builder for Windows x64

## Document Processing Flow Pattern

The main document views (`SalesView.jsx`, `PurchaseInvoiceView.jsx`, `BankEntriesView.jsx`) follow a consistent pattern:

1. **Upload PDF** → User selects company and uploads document
2. **Extract Data** → Backend extracts transactions using AI/OCR
3. **Match Ledgers** → Backend matches parties to existing Tally ledgers
4. **Edit Table** → User reviews/edits extracted data in resizable panel layout
5. **Push to Tally** → Generate XML, push via websocket

**Resizable Panels:**
```javascript
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from 'react-resizable-panels';
// Note: Use "Group as PanelGroup" not "PanelGroup" directly
```

**Table input styling:** Use fixed-width classes (`w-20`, `w-24`) not `w-full` for number inputs in tables.

## Code Conventions

- Page components in `components/Pages/`
- Reusable UI in `components/ui/`
- All API calls through `services/api.js`
- Context providers wrap the app in `index.js`
- Use `ProtectedRoute` for authenticated routes
- Token management centralized in `services/auth.js`

## Common Pitfalls

- **Tally "offline" in production**: Use websocket-based endpoints, not direct HTTP
- **Validation errors blocking push**: Check if errors are connection-related and skip
- **PanelGroup import error**: Import as `{ Group as PanelGroup }` from react-resizable-panels
- **Numbers not visible in tables**: Use fixed-width inputs, not w-full
