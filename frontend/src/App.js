/* eslint-disable */
import './App.css';
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import LoginModalCA from './components/Pages/EazyBooksCA/LoginModalCA';
import EazyBooksCA from "./components/Pages/EazyBooksCA/EazyBooksCA";
import ChatbotLight from "./components/Pages/ChatBot/ChatbotLight";
import LockrPage from "./components/Pages/EazyBooksCA/Lockr";
import ClientsPage from "./components/Pages/EazyBooksCA/Clients";
import HistoryPage from "./components/Pages/EazyBooksCA/History";
import NoticeAidPage from "./components/Pages/EazyBooksCA/NoticeAid";
import CloudPage from "./components/Pages/EazyBooksCA/Cloud";
import TallyPage from "./components/Pages/EazyBooksCA/Tally";
import TallyChatPage from "./components/Pages/EazyBooksCA/TallyChat";
import QBDashboard from "./components/Pages/EazyBooksCA/QB/QBDashboard";
import QBSalesView from "./components/Pages/EazyBooksCA/QB/QBSalesView";
import QBPurchaseView from "./components/Pages/EazyBooksCA/QB/QBPurchaseView";
import QBBankReconView from "./components/Pages/EazyBooksCA/QB/QBBankReconView";
import QBBankRecon from "./components/Pages/EazyBooksCA/QB/QBBankRecon";
import QBBankEntriesView from "./components/Pages/EazyBooksCA/QB/QBBankEntriesView";
import QBChat from "./components/Pages/EazyBooksCA/QB/QBChat";
import ZohoDashboard from "./components/Pages/EazyBooksCA/Zoho/ZohoDashboard";
import ZohoSalesView from "./components/Pages/EazyBooksCA/Zoho/ZohoSalesView";
import ZohoPurchaseView from "./components/Pages/EazyBooksCA/Zoho/ZohoPurchaseView";
import ZohoBankReconView from "./components/Pages/EazyBooksCA/Zoho/ZohoBankReconView";
import ZohoBankEntriesView from "./components/Pages/EazyBooksCA/Zoho/ZohoBankEntriesView";
import ZohoChat from "./components/Pages/EazyBooksCA/Zoho/ZohoChat";
import ZohoBankRecon from "./components/Pages/EazyBooksCA/Zoho/ZohoBankRecon";
import XeroDashboard from "./components/Pages/EazyBooksCA/Xero/XeroDashboard";
import XeroSalesView from "./components/Pages/EazyBooksCA/Xero/XeroSalesView";
import XeroPurchaseView from "./components/Pages/EazyBooksCA/Xero/XeroPurchaseView";
import XeroBankReconView from "./components/Pages/EazyBooksCA/Xero/XeroBankReconView";
import XeroBankEntriesView from "./components/Pages/EazyBooksCA/Xero/XeroBankEntriesView";
import XeroChat from "./components/Pages/EazyBooksCA/Xero/XeroChat";
import XeroBankRecon from "./components/Pages/EazyBooksCA/Xero/XeroBankRecon";
import SAPDashboard from "./components/Pages/EazyBooksCA/SAP/SAPDashboard";
import SAPSalesView from "./components/Pages/EazyBooksCA/SAP/SAPSalesView";
import SAPPurchaseView from "./components/Pages/EazyBooksCA/SAP/SAPPurchaseView";
import SAPBankReconView from "./components/Pages/EazyBooksCA/SAP/SAPBankReconView";
import SAPBankEntriesView from "./components/Pages/EazyBooksCA/SAP/SAPBankEntriesView";
import SAPChat from "./components/Pages/EazyBooksCA/SAP/SAPChat";
import CloudDraftLibrary from "./components/Pages/EazyBooksCA/common/CloudDraftLibrary";
import MISPage from "./components/Pages/EazyBooksCA/MIS";
import Dashboard from "./components/Pages/EazyBooksCA/Dashboard/Dashboard";
import PlatformPicker from "./components/Pages/EazyBooksCA/Workspace/PlatformPicker";
import WorkspaceGate from "./components/Pages/EazyBooksCA/Workspace/WorkspaceGate";
import SettingsLayout from "./components/Pages/EazyBooksCA/Settings/SettingsLayout";
import AccountPanel from "./components/Pages/EazyBooksCA/Settings/panels/AccountPanel";
import ConnectionsPanel from "./components/Pages/EazyBooksCA/Settings/panels/ConnectionsPanel";
import TeamLanding from "./components/Pages/EazyBooksCA/Settings/panels/TeamLanding";
import UsersPage from "./components/Pages/Admin/UsersPage";
import RolesPage from "./components/Pages/Admin/RolesPage";
import PermissionsPage from "./components/Pages/Admin/PermissionsPage";
import AuditLogPage from "./components/Pages/Admin/AuditLogPage";
import AdminOverviewPage from "./components/Pages/Admin/AdminOverviewPage";
import NotAuthorized from "./components/Pages/Admin/NotAuthorized";
import React, { useContext } from "react";
import { EazyBooksContext } from './components/context/EazyBooksContext';
import { WorkspaceProvider } from './components/context/WorkspaceContext';
import LandingPage from './components/Pages/Landing/LandingPage';
import ProtectedRoute from './components/auth/ProtectedRoute';
import Login from './components/Pages/Auth/Login';
import Signup from './components/Pages/Auth/Signup';
import PrivacyPolicy from './components/Pages/Legal/PrivacyPolicy';
import EndUserAgreement from './components/Pages/Legal/EndUserAgreement';
import { RouteGuard } from './components/rbac';

// Shared authenticated shell for the whole dashboard area. One WorkspaceProvider
// wraps BOTH the full-screen platform picker (/dashboard/select) and the
// sidebar shell (/dashboard/*), so the active-platform choice survives the move
// between them.
function DashboardLayout() {
  return (
    <ProtectedRoute>
      <WorkspaceProvider>
        <Outlet />
      </WorkspaceProvider>
    </ProtectedRoute>
  );
}

function App() {
  const context = useContext(EazyBooksContext);
  const { isOpen } = context;
  const location = useLocation();

  return (
    <div className="App">
      {isOpen && <LoginModalCA />}
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/privacy-policy" element={<PrivacyPolicy />} />
        <Route path="/end-user-agreement" element={<EndUserAgreement />} />
        <Route element={<DashboardLayout />}>
          {/* Full-screen workspace picker — lives outside the EazyBooksCA shell
              (no contextual sidebar; you're choosing which workspace to enter).
              Ranks above the /dashboard/* splat below. */}
          <Route path="/dashboard/select" element={<PlatformPicker />} />

          <Route path="/dashboard/*" element={<EazyBooksCA />}>
            {/* Landing decision: remembered workspace → straight in; otherwise
                → the picker. */}
            <Route index element={<WorkspaceGate />} />
            <Route path="chat" element={<ChatbotLight />} />
          {/* Workspace features hidden until a later phase. Page components
              (LockrPage, ClientsPage, HistoryPage, NoticeAidPage, CloudPage)
              are preserved; their routes redirect to the dashboard so the
              paths are inaccessible. Re-enable the lines below to restore. */}
          {/* <Route path="lockr" element={<LockrPage />} /> */}
          {/* <Route path="clients" element={<ClientsPage />} /> */}
          {/* <Route path="history" element={<HistoryPage />} /> */}
          {/* <Route path="notice-aid" element={<NoticeAidPage />} /> */}
          {/* <Route path="cloud" element={<CloudPage />} /> */}
          <Route path="lockr" element={<Navigate to="/dashboard" replace />} />
          <Route path="clients" element={<Navigate to="/dashboard" replace />} />
          <Route path="history" element={<Navigate to="/dashboard" replace />} />
          <Route path="notice-aid" element={<Navigate to="/dashboard" replace />} />
          <Route path="cloud" element={<Navigate to="/dashboard" replace />} />
          <Route path="tally" element={<TallyPage />} />
          <Route path="tally-chat" element={<TallyChatPage />} />
          <Route path="qb" element={<QBDashboard />} />
          <Route path="qb/sales" element={<QBSalesView />} />
          <Route path="qb/purchase" element={<QBPurchaseView />} />
          <Route path="qb/bank-recon" element={<QBBankRecon />} />
          <Route path="qb/bank-entries" element={<QBBankEntriesView />} />
          {/* Drafts are now embedded in the QB dashboard (Tally-parity);
              redirect any old bookmarks/deeplinks to /dashboard/qb. */}
          <Route path="qb/drafts" element={<Navigate to="/dashboard/qb" replace />} />
          <Route path="qb-chat" element={<QBChat />} />
          <Route path="zoho" element={<ZohoDashboard />} />
          <Route path="zoho/sales" element={<ZohoSalesView />} />
          <Route path="zoho/purchase" element={<ZohoPurchaseView />} />
          <Route path="zoho/bank-recon" element={<ZohoBankRecon />} />
          <Route path="zoho/bank-entries" element={<ZohoBankEntriesView />} />
          <Route path="zoho/drafts" element={<CloudDraftLibrary platform="zoho" />} />
          <Route path="zoho-chat" element={<ZohoChat />} />
          <Route path="xero" element={<XeroDashboard />} />
          <Route path="xero/sales" element={<XeroSalesView />} />
          <Route path="xero/purchase" element={<XeroPurchaseView />} />
          <Route path="xero/bank-recon" element={<XeroBankRecon />} />
          <Route path="xero/bank-entries" element={<XeroBankEntriesView />} />
          <Route path="xero/drafts" element={<CloudDraftLibrary platform="xero" />} />
          <Route path="xero-chat" element={<XeroChat />} />
          <Route path="sap" element={<SAPDashboard />} />
          <Route path="sap/sales" element={<SAPSalesView />} />
          <Route path="sap/purchase" element={<SAPPurchaseView />} />
          <Route path="sap/bank-recon" element={<SAPBankReconView />} />
          <Route path="sap/bank-entries" element={<SAPBankEntriesView />} />
          <Route path="sap/drafts" element={<CloudDraftLibrary platform="sap" />} />
          <Route path="sap-chat" element={<SAPChat />} />
          {/* MIS hidden until a later phase. MISPage component is preserved;
              route redirects to the dashboard. Re-enable to restore. */}
          {/* <Route path="mis" element={<MISPage />} /> */}
          <Route path="mis" element={<Navigate to="/dashboard" replace />} />
          {/* Per-platform analytics (scoped to the active workspace) + the
              distinct aggregated "All ledgers" overview. */}
          <Route path="analytics" element={<Dashboard scope="platform" />} />
          <Route path="analytics/all" element={<Dashboard scope="all" />} />

          {/* Account-level Settings (outside the workspace context):
              Account · Connections · Team. The RBAC admin pages are folded
              under Team and gated per-page through RouteGuard. */}
          <Route path="settings" element={<SettingsLayout />}>
            <Route index element={<Navigate to="account" replace />} />
            <Route path="account" element={<AccountPanel />} />
            <Route path="connections" element={<ConnectionsPanel />} />
            <Route path="team" element={<TeamLanding />} />
            <Route element={<RouteGuard permission="data.read" />}>
              <Route path="team/overview" element={<AdminOverviewPage />} />
            </Route>
            <Route element={<RouteGuard permission="users.read" />}>
              <Route path="team/users" element={<UsersPage />} />
            </Route>
            <Route element={<RouteGuard permission="roles.read" />}>
              <Route path="team/roles" element={<RolesPage />} />
            </Route>
            <Route element={<RouteGuard permission="permissions.read" />}>
              <Route path="team/permissions" element={<PermissionsPage />} />
            </Route>
            <Route element={<RouteGuard permission="audit.read" />}>
              <Route path="team/audit" element={<AuditLogPage />} />
            </Route>
          </Route>

          {/* Legacy admin paths now live under Settings → Team — redirect old
              bookmarks/deeplinks to the new locations. */}
          <Route path="admin/users" element={<Navigate to="/dashboard/settings/team/users" replace />} />
          <Route path="admin/roles" element={<Navigate to="/dashboard/settings/team/roles" replace />} />
          <Route path="admin/permissions" element={<Navigate to="/dashboard/settings/team/permissions" replace />} />
          <Route path="admin/audit" element={<Navigate to="/dashboard/settings/team/audit" replace />} />
          <Route path="403" element={<NotAuthorized />} />
          </Route>
        </Route>
      </Routes>
    </div>
  );
}

export default App;
