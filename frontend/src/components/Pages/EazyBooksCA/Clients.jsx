import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Users,
  FolderOpen,
  Plus,
  MoreVertical,
  Trash2,
  AlertCircle,
  CheckCircle2,
  MessageSquare,
  FileText,
} from "lucide-react";
import ClientPage from "./ClientPage";
import CreateClient from "./CreateClient";
import { clientsApi } from "../../../services/api";
import { PageHeader } from "../../ui/page-header";
import { Button } from "../../ui/button";
import { Card } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { SkeletonCard } from "../../ui/skeleton";
import { EmptyState } from "../../ui/empty-state";
import { cn } from "../../../lib/utils";
import { usePermissions, PermissionGuard } from "../../rbac";

export default function ClientsPage() {
  const navigate = useNavigate();
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission("clients.create");
  const canDelete = hasPermission("clients.delete");

  const [clients, setClients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedClient, setSelectedClient] = useState(null);
  const [showCreateClient, setShowCreateClient] = useState(false);
  const [openMenuId, setOpenMenuId] = useState(null);

  useEffect(() => {
    const handleClickOutside = () => setOpenMenuId(null);
    document.addEventListener("click", handleClickOutside);
    return () => document.removeEventListener("click", handleClickOutside);
  }, []);

  const fetchData = async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await clientsApi.getClientsWithDetails();
      setClients(result.clients || []);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleDelete = async (clientId, e) => {
    e.stopPropagation();
    try {
      await clientsApi.deleteClient(clientId);
      fetchData();
    } catch (err) {
      console.error("Error deleting client:", err);
      alert("Failed to delete client. Please try again.");
    }
  };

  if (selectedClient) {
    return <ClientPage client={selectedClient} onBack={() => setSelectedClient(null)} />;
  }

  if (showCreateClient) {
    return (
      <CreateClient
        onBack={async () => {
          setShowCreateClient(false);
          await new Promise((resolve) => setTimeout(resolve, 500));
          fetchData();
        }}
      />
    );
  }

  return (
    <div className="min-h-screen">
      <PageHeader
        icon={Users}
        title="Clients"
        description="Manage client profiles, files, and conversations in one place."
        actions={
          <PermissionGuard permission="clients.create">
            <Button
              variant="primary"
              leftIcon={<Plus />}
              onClick={() => setShowCreateClient(true)}
            >
              Add client
            </Button>
          </PermissionGuard>
        }
      />

      <div className="px-6 lg:px-8 py-6">
        {/* Stat row */}
        {!loading && !error && clients.length > 0 && (
          <div className="mb-6 flex items-center gap-4 text-sm text-gilt-soft">
            <span>
              <span className="text-gilt-ink font-semibold font-mono tabular-nums">{clients.length}</span>{" "}
              {clients.length === 1 ? "client" : "clients"}
            </span>
          </div>
        )}

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
              Couldn't load clients: {error.message || "Unknown error"}
            </p>
            <Button
              variant="secondary"
              size="sm"
              className="mt-3"
              onClick={fetchData}
            >
              Try again
            </Button>
          </Card>
        ) : clients.length === 0 ? (
          <Card padded>
            <EmptyState
              icon={Users}
              title="No clients yet"
              description="Create your first client to start organizing their files, chats, and Tally companies."
              action={
                canCreate ? (
                  <Button leftIcon={<Plus />} onClick={() => setShowCreateClient(true)}>
                    Add your first client
                  </Button>
                ) : null
              }
            />
          </Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {clients.map((client, index) => {
              const files = client.files || [];
              const chats = client.chats || [];
              const allSynced = files.length > 0 && files.every((f) => f.is_synced);
              const someUnsynced = files.length > 0 && !allSynced;
              return (
                <Card
                  key={client.id || client.client_id || index}
                  interactive
                  className="relative group p-5"
                  onClick={() => setSelectedClient(client)}
                >
                  <div className="flex items-start justify-between mb-4">
                    <div className="h-10 w-10 rounded-full bg-gilt-sunken border border-gilt-hairline text-gilt-brass flex items-center justify-center">
                      <FolderOpen size={18} strokeWidth={1.75} />
                    </div>
                    <div className="relative">
                      {canDelete && (
                        <button
                          type="button"
                          className="h-7 w-7 inline-flex items-center justify-center rounded-md text-gilt-faint hover:bg-gilt-sunken hover:text-gilt-soft transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                          onClick={(e) => {
                            e.stopPropagation();
                            e.nativeEvent.stopImmediatePropagation();
                            setOpenMenuId((prev) => (prev === index ? null : index));
                          }}
                          aria-label="Client actions"
                        >
                          <MoreVertical size={14} />
                        </button>
                      )}
                      {openMenuId === index && (
                        <div
                          className="absolute right-0 top-full mt-1 w-40 bg-gilt-card rounded-lg shadow-sm border border-gilt-hairline py-1 z-10 origin-top-right"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDelete(client.client_id, e);
                              setOpenMenuId(null);
                            }}
                            className="w-full text-left px-3 py-1.5 text-sm text-gilt-debit hover:bg-gilt-sunken flex items-center gap-2 transition-colors"
                          >
                            <Trash2 size={13} />
                            Delete client
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  <h3 className="text-sm font-semibold text-gilt-ink truncate mb-1">
                    {client.name || "Untitled client"}
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
