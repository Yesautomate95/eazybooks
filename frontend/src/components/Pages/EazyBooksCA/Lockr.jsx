import { useState, useEffect, useContext } from "react";
import { useNavigate } from "react-router-dom";
import {
  Lock,
  BookOpen,
  Plus,
  MoreVertical,
  Trash2,
  Link as LinkIcon,
  AlertCircle,
  CheckCircle2,
  Download,
  ShieldCheck,
  RefreshCw,
  FileText,
  MessageSquare,
} from "lucide-react";
import ProjectPage from "./ProjectPage";
import CreateLocker from "./CreateLocker";
import { EazyBooksContext } from "../../context/EazyBooksContext";
import { clientsApi, tallyApi } from "../../../services/api";
import { PageHeader } from "../../ui/page-header";
import { Button } from "../../ui/button";
import { Card } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { SkeletonCard } from "../../ui/skeleton";
import { EmptyState } from "../../ui/empty-state";
import { cn } from "../../../lib/utils";
import { errorText } from "./common/errorText";
import { usePermissions, PermissionGuard } from "../../rbac";

const TALLY_VISUALS = {
  connected: {
    badgeVariant: "success",
    label: "Connected",
    description: "Syncing data with Tally automatically.",
    cta: "Sync companies",
  },
  connecting: {
    badgeVariant: "info",
    label: "Connecting…",
    description: "Establishing a secure channel with your Tally instance.",
    cta: "Connecting…",
  },
  error: {
    badgeVariant: "danger",
    label: "Connection failed",
    description: "We couldn't reach Tally. Make sure the desktop app is running.",
    cta: "Retry",
  },
  disconnected: {
    badgeVariant: "neutral",
    label: "Not connected",
    description: "Connect your Tally instance to sync companies into projects.",
    cta: "Connect to Tally",
  },
};

export default function VaultPage() {
  const navigate = useNavigate();
  const { user } = useContext(EazyBooksContext);
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission("documents.create");
  const canDelete = hasPermission("documents.delete");
  const canSyncTally = hasPermission("tally.sync");
  const canConnectTally = hasPermission("tally.read");
  const [lockers, setLockers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedProject, setSelectedProject] = useState(null);
  const [showCreateLocker, setShowCreateLocker] = useState(false);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [tallyStatus, setTallyStatus] = useState("disconnected");
  const [desktopAppVersion, setDesktopAppVersion] = useState(null);

  useEffect(() => {
    const handleClickOutside = () => setOpenMenuId(null);
    document.addEventListener("click", handleClickOutside);
    return () => document.removeEventListener("click", handleClickOutside);
  }, []);

  const fetchData = async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await clientsApi.getLockersWithDetails();
      setLockers(result.lockers || []);
    } catch (err) {
      console.error("Error fetching lockers:", err);
      setError(errorText(err, "Unknown error"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleDelete = async (lockerId, e) => {
    e.stopPropagation();
    try {
      await clientsApi.deleteLocker(lockerId);
      fetchData();
    } catch (err) {
      console.error("Error deleting locker:", err);
      alert("Failed to delete locker. Please try again.");
    }
  };

  const handleFileDelete = (fileId) => {
    if (selectedProject) {
      setSelectedProject((prev) => ({
        ...prev,
        files: prev.files.filter((file) => file.file_id !== fileId),
      }));
    }
    setLockers((prev) =>
      prev.map((locker) => {
        if (locker.lockr_id === selectedProject?.lockr_id) {
          return {
            ...locker,
            files: locker.files.filter((file) => file.file_id !== fileId),
          };
        }
        return locker;
      })
    );
  };

  const checkTallyConnection = async () => {
    try {
      let isConnected = false;
      try {
        const directResult = await tallyApi.checkDirectConnection();
        isConnected = directResult?.connected ?? false;
      } catch {
        try {
          const wsResult = await tallyApi.checkConnection();
          isConnected = wsResult?.connected ?? false;
        } catch {
          isConnected = false;
        }
      }
      setTallyStatus(isConnected ? "connected" : "disconnected");
    } catch (error) {
      console.error("Error checking Tally connection:", error);
      setTallyStatus("error");
    }
  };

  const syncCompanyFolders = async () => {
    try {
      const result = await tallyApi.syncCompanyFolders();
      localStorage.removeItem("lockers");
      localStorage.removeItem("lockers_timestamp");
      await fetchData();
      const { created, existing } = result;
      alert(
        `Successfully synced with Tally!\nCreated ${created.count} new folders\nFound ${existing.count} existing folders`
      );
    } catch (error) {
      console.error("Error syncing company folders:", error);
      if (error.response?.status === 400) alert("Please connect to Tally first");
      else if (error.response?.status === 404) alert("No companies found in Tally");
      else alert("Error syncing with Tally. Please try again.");
    }
  };

  useEffect(() => {
    checkTallyConnection();
  }, []);

  const handleTallyConnection = async () => {
    if (tallyStatus === "connected") {
      try {
        await syncCompanyFolders();
      } catch (error) {
        console.error("Failed to sync companies:", error);
        setTallyStatus("error");
      }
      return;
    }
    if (tallyStatus === "error") {
      setTallyStatus("disconnected");
      return;
    }
    setTallyStatus("connecting");
    try {
      await checkTallyConnection();
    } catch (error) {
      console.error("Failed to connect to Tally:", error);
      setTallyStatus("error");
    }
  };

  const fetchLatestVersion = async () => {
    try {
      const result = await tallyApi.getDesktopVersion();
      setDesktopAppVersion(result.success ? result.version : "1.0.0");
    } catch (error) {
      console.error("Error fetching latest version:", error);
      setDesktopAppVersion("1.0.0");
    }
  };

  useEffect(() => {
    fetchLatestVersion();
  }, []);

  if (selectedProject) {
    return (
      <ProjectPage
        project={selectedProject}
        onBack={() => setSelectedProject(null)}
        onFileDelete={handleFileDelete}
      />
    );
  }

  if (showCreateLocker) {
    return (
      <CreateLocker
        onBack={async () => {
          setShowCreateLocker(false);
          await new Promise((resolve) => setTimeout(resolve, 500));
          fetchData();
        }}
      />
    );
  }

  const tallyVis = TALLY_VISUALS[tallyStatus] || TALLY_VISUALS.disconnected;

  return (
    <div className="min-h-screen">
      <PageHeader
        icon={ShieldCheck}
        title="Vault"
        description="Securely store thousands of documents and let AI search across them."
        actions={
          <PermissionGuard permission="documents.create">
            <Button leftIcon={<Plus />} onClick={() => setShowCreateLocker(true)}>
              New project
            </Button>
          </PermissionGuard>
        }
      />

      <div className="px-6 lg:px-8 py-6 space-y-6">
        {/* Integration row */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Tally connection card */}
          <Card padded className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3 min-w-0">
              <div
                className={cn(
                  "shrink-0 h-10 w-10 rounded-lg flex items-center justify-center border bg-gilt-sunken",
                  tallyStatus === "connected" && "border-gilt-credit/40 text-gilt-credit",
                  tallyStatus === "connecting" && "border-gilt-brass/40 text-gilt-brass",
                  tallyStatus === "error" && "border-gilt-debit/40 text-gilt-debit",
                  tallyStatus === "disconnected" && "border-gilt-hairline text-gilt-soft"
                )}
              >
                <LinkIcon size={18} strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="text-sm font-semibold text-gilt-ink">Tally connection</h3>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded border font-mono text-[10px] uppercase tracking-[0.14em] whitespace-nowrap",
                      tallyVis.badgeVariant === "success" && "text-gilt-credit border-gilt-credit/40",
                      tallyVis.badgeVariant === "info" && "text-gilt-brass border-gilt-brass/40",
                      tallyVis.badgeVariant === "danger" && "text-gilt-debit border-gilt-debit/40",
                      tallyVis.badgeVariant === "neutral" && "text-gilt-soft border-gilt-hairline"
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        "w-1.5 h-1.5 rounded-full",
                        tallyVis.badgeVariant === "success" && "bg-gilt-credit",
                        tallyVis.badgeVariant === "info" && "bg-gilt-brass",
                        tallyVis.badgeVariant === "danger" && "bg-gilt-debit",
                        tallyVis.badgeVariant === "neutral" && "bg-gilt-faint"
                      )}
                    />
                    {tallyVis.label}
                  </span>
                </div>
                <p className="mt-1 text-xs text-gilt-soft">{tallyVis.description}</p>
              </div>
            </div>
            {(tallyStatus === "connected" ? canSyncTally : canConnectTally) && (
              <Button
                size="sm"
                onClick={handleTallyConnection}
                loading={tallyStatus === "connecting"}
                leftIcon={tallyStatus === "connected" ? <RefreshCw /> : <LinkIcon />}
              >
                {tallyVis.cta}
              </Button>
            )}
          </Card>

          {/* Desktop app card */}
          <Card padded className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3 min-w-0">
              <div className="shrink-0 h-10 w-10 rounded-lg bg-gilt-sunken border border-gilt-hairline text-gilt-brass flex items-center justify-center">
                <Download size={18} strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="text-sm font-semibold text-gilt-ink">Desktop app</h3>
                  <Badge variant="brand" size="sm" className="font-mono text-[10px] uppercase tracking-[0.14em] tabular-nums">
                    {desktopAppVersion ? `v${desktopAppVersion}` : "Required"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-gilt-soft">
                  Required bridge between EazyBooks and your local Tally installation.
                </p>
              </div>
            </div>
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Download />}
              onClick={() =>
                window.open(
                  `${process.env.REACT_APP_API_URL}/ca/desktop/download/${
                    desktopAppVersion || "1.0.0"
                  }`,
                  "_blank"
                )
              }
            >
              Download
            </Button>
          </Card>
        </div>

        {/* Projects header */}
        <div className="flex items-end justify-between pt-2">
          <div>
            <h2 className="text-lg font-semibold text-gilt-ink">Your projects</h2>
            {!loading && !error && (
              <p className="text-xs text-gilt-soft mt-0.5 font-mono tabular-nums">
                {lockers.length} {lockers.length === 1 ? "project" : "projects"}
              </p>
            )}
          </div>
        </div>

        {/* Grid */}
        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : error ? (
          <Card padded className="border-gilt-debit/40 bg-gilt-sunken">
            <p className="text-sm text-gilt-debit">
              Couldn't load projects: {error}
            </p>
            <Button variant="secondary" size="sm" className="mt-3" onClick={fetchData}>
              Try again
            </Button>
          </Card>
        ) : lockers.length === 0 ? (
          <Card padded>
            <EmptyState
              icon={Lock}
              title="No projects yet"
              description="Create a project to upload a collection of files, search them with AI, and push entries to Tally."
              action={
                canCreate ? (
                  <Button leftIcon={<Plus />} onClick={() => setShowCreateLocker(true)}>
                    Create your first project
                  </Button>
                ) : null
              }
            />
          </Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {lockers.map((locker, index) => {
              const files = Array.isArray(locker.files) ? locker.files : [];
              const chats = Array.isArray(locker.chats) ? locker.chats : [];
              const allSynced = files.length > 0 && files.every((f) => f.is_synced);
              return (
                <Card
                  key={locker.lockr_id || index}
                  interactive
                  className="relative group p-5"
                  onClick={() => setSelectedProject(locker)}
                >
                  <div className="flex items-start justify-between mb-4">
                    <div className="h-10 w-10 rounded-lg bg-gilt-sunken border border-gilt-hairline text-gilt-brass flex items-center justify-center">
                      <BookOpen size={18} strokeWidth={1.75} />
                    </div>
                    <div className="relative">
                      {canDelete && (
                        <button
                          type="button"
                          className="h-7 w-7 inline-flex items-center justify-center rounded-md text-gilt-faint hover:bg-gilt-sunken hover:text-gilt-ink transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                          onClick={(e) => {
                            e.stopPropagation();
                            e.nativeEvent.stopImmediatePropagation();
                            setOpenMenuId((prev) =>
                              prev === locker.lockr_id ? null : locker.lockr_id
                            );
                          }}
                          aria-label="Project actions"
                        >
                          <MoreVertical size={14} />
                        </button>
                      )}
                      {openMenuId === locker.lockr_id && (
                        <div
                          className="absolute right-0 top-full mt-1 w-40 bg-gilt-card rounded-lg shadow-sm border border-gilt-hairline py-1 z-10"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDelete(locker.lockr_id, e);
                              setOpenMenuId(null);
                            }}
                            className="w-full text-left px-3 py-1.5 text-sm text-gilt-debit hover:bg-gilt-sunken flex items-center gap-2 transition-colors"
                          >
                            <Trash2 size={13} />
                            Delete project
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  <h3 className="text-sm font-semibold text-gilt-ink truncate mb-1">
                    {locker.name || "Untitled project"}
                  </h3>

                  <div className="flex items-center gap-3 text-xs text-gilt-soft mb-3">
                    <span className="inline-flex items-center gap-1 font-mono tabular-nums">
                      <FileText size={12} className="text-gilt-faint" />
                      {files.length}
                    </span>
                    <span className="inline-flex items-center gap-1 font-mono tabular-nums">
                      <MessageSquare size={12} className="text-gilt-faint" />
                      {chats.length}
                    </span>
                  </div>

                  {files.length > 0 && (
                    <Badge
                      variant={allSynced ? "success" : "warning"}
                      size="sm"
                      dot
                      className="font-mono text-[10px] uppercase tracking-[0.14em]"
                    >
                      {allSynced ? "All files synced" : "Sync pending"}
                    </Badge>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
