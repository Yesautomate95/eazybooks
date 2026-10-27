import React, { useEffect, useMemo, useState } from "react";
import {
  ShieldCheck,
  Plus,
  Search,
  Copy,
  Trash2,
  Pencil,
  Users as UsersIcon,
  KeyRound,
  Save,
} from "lucide-react";
import { rbacApi } from "../../../services/api";
import { PageHeader } from "../../ui/page-header";
import { Card } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Input, Label, Textarea } from "../../ui/input";
import { Skeleton } from "../../ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../../ui/dialog";
import { useRbac } from "../../rbac";
import { cn } from "../../../lib/utils";

/**
 * Roles management.
 *
 * Layout:
 *  - Left rail: list of roles with user counts. Selecting a role loads detail.
 *  - Right pane: role detail — name, description, permission matrix grouped
 *    by module. Permissions are bulk-saved.
 *
 * Two dialogs: Create role, Clone role. Edits to system roles are blocked
 * server-side, but we also disable the controls client-side for a clean UX.
 */
export default function RolesPage() {
  const { hasPermission, refresh: refreshRbac } = useRbac();
  const canCreate = hasPermission("roles.create");
  const canUpdate = hasPermission("roles.update");
  const canDelete = hasPermission("roles.delete");
  const canClone = hasPermission("roles.clone");

  const [roles, setRoles] = useState([]);
  const [permissions, setPermissions] = useState([]);
  const [loadingRoles, setLoadingRoles] = useState(true);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");

  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // edit form mirror — kept separate from `detail` so we can save/diff cleanly
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editPermKeys, setEditPermKeys] = useState(new Set());
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const loadRoles = async () => {
    setLoadingRoles(true);
    setError("");
    try {
      const data = await rbacApi.listRoles();
      setRoles(data.items || []);
      if (!selectedId && data.items?.length) setSelectedId(data.items[0].role_id);
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err?.response?.data?.detail || err.message);
    } finally {
      setLoadingRoles(false);
    }
  };

  const loadPermissions = async () => {
    try {
      const data = await rbacApi.listPermissions();
      setPermissions(data.items || []);
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    }
  };

  useEffect(() => {
    loadRoles();
    loadPermissions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadDetail = async (roleId) => {
    if (!roleId) {
      setDetail(null);
      return;
    }
    setLoadingDetail(true);
    setError("");
    try {
      const role = await rbacApi.getRole(roleId);
      setDetail(role);
      setEditName(role.name || "");
      setEditDescription(role.description || "");
      setEditPermKeys(new Set((role.permissions || []).map((p) => p.key)));
      setDirty(false);
      setSavedAt(null);
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    } finally {
      setLoadingDetail(false);
    }
  };

  useEffect(() => {
    loadDetail(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const filteredRoles = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return roles;
    return roles.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.key.toLowerCase().includes(q) ||
        r.description?.toLowerCase().includes(q)
    );
  }, [roles, search]);

  const permsByModule = useMemo(() => {
    const map = new Map();
    permissions.forEach((p) => {
      const list = map.get(p.module) || [];
      list.push(p);
      map.set(p.module, list);
    });
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [permissions]);

  const togglePerm = (key) => {
    if (!canUpdate || detail?.is_system) return;
    setEditPermKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setDirty(true);
  };

  const toggleModule = (modulePerms, allOn) => {
    if (!canUpdate || detail?.is_system) return;
    setEditPermKeys((prev) => {
      const next = new Set(prev);
      modulePerms.forEach((p) => {
        if (allOn) next.delete(p.key);
        else next.add(p.key);
      });
      return next;
    });
    setDirty(true);
  };

  const onSave = async () => {
    if (!detail) return;
    setSaving(true);
    setError("");
    try {
      // Save name/description first if changed
      const nameChanged = editName !== detail.name;
      const descChanged = editDescription !== (detail.description || "");
      if (nameChanged || descChanged) {
        await rbacApi.updateRole(detail.role_id, {
          name: nameChanged ? editName : undefined,
          description: descChanged ? editDescription : undefined,
        });
      }
      // Always replace permissions (idempotent)
      await rbacApi.setRolePermissions(detail.role_id, Array.from(editPermKeys));
      setSavedAt(new Date());
      setDirty(false);
      await Promise.all([loadRoles(), loadDetail(detail.role_id), refreshRbac()]);
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (!detail) return;
    try {
      await rbacApi.deleteRole(detail.role_id);
      setDeleteOpen(false);
      setSelectedId(null);
      await loadRoles();
      await refreshRbac();
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    }
  };

  return (
    <div className="min-h-screen">
      <PageHeader
        icon={ShieldCheck}
        title="Roles"
        description="Group permissions into roles, then assign roles to users."
        actions={
          canCreate && (
            <Button leftIcon={<Plus />} onClick={() => setCreateOpen(true)}>
              New role
            </Button>
          )
        }
      />

      <div className="px-6 lg:px-8 py-6">
        {error && (
          <Card padded className="mb-4 border-gilt-debit/40 bg-gilt-sunken">
            <p className="text-sm text-gilt-debit">{error}</p>
          </Card>
        )}

        <div className="grid grid-cols-12 gap-6">
          {/* Left rail */}
          <div className="col-span-12 md:col-span-4 lg:col-span-3">
            <Card>
              <div className="p-3 border-b border-gilt-hairline">
                <Input
                  size="sm"
                  leftIcon={<Search />}
                  placeholder="Search roles…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div className="max-h-[70vh] overflow-y-auto">
                {loadingRoles ? (
                  <div className="p-3 space-y-2">
                    {Array.from({ length: 4 }).map((_, i) => (
                      <Skeleton key={i} className="h-9 w-full" />
                    ))}
                  </div>
                ) : filteredRoles.length === 0 ? (
                  <p className="text-sm text-gilt-soft text-center py-8">No roles</p>
                ) : (
                  <ul className="divide-y divide-gilt-hairline">
                    {filteredRoles.map((r) => {
                      const active = selectedId === r.role_id;
                      return (
                        <li key={r.role_id}>
                          <button
                            type="button"
                            onClick={() => setSelectedId(r.role_id)}
                            className={cn(
                              "w-full text-left px-3 py-2.5 hover:bg-gilt-sunken transition-colors flex items-start gap-2",
                              active && "bg-gilt-sunken"
                            )}
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span
                                  className={cn(
                                    "text-sm font-medium truncate",
                                    active ? "text-gilt-brass" : "text-gilt-ink"
                                  )}
                                >
                                  {r.name}
                                </span>
                                {r.is_system && (
                                  <Badge variant="brand" size="sm">System</Badge>
                                )}
                              </div>
                              <p className="text-2xs text-gilt-soft truncate mt-0.5">
                                <span className="inline-flex items-center gap-1 font-mono tabular-nums">
                                  <UsersIcon size={11} />
                                  {r.user_count}
                                </span>
                                <span className="mx-1.5">·</span>
                                <span className="inline-flex items-center gap-1 font-mono tabular-nums">
                                  <KeyRound size={11} />
                                  {r.permission_count}
                                </span>
                              </p>
                            </div>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </Card>
          </div>

          {/* Right pane */}
          <div className="col-span-12 md:col-span-8 lg:col-span-9">
            {!selectedId ? (
              <Card padded>
                <p className="text-sm text-gilt-soft text-center py-10">
                  Select a role on the left to edit it.
                </p>
              </Card>
            ) : loadingDetail || !detail ? (
              <Card padded>
                <Skeleton className="h-5 w-1/3 mb-4" />
                <Skeleton className="h-3 w-2/3 mb-8" />
                <Skeleton className="h-24 w-full" />
              </Card>
            ) : (
              <div className="space-y-4">
                <Card padded>
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0 space-y-3">
                      <div>
                        <Label>Role name</Label>
                        <Input
                          value={editName}
                          onChange={(e) => {
                            setEditName(e.target.value);
                            setDirty(true);
                          }}
                          disabled={detail.is_system || !canUpdate}
                        />
                      </div>
                      <div>
                        <Label>Description</Label>
                        <Textarea
                          value={editDescription}
                          onChange={(e) => {
                            setEditDescription(e.target.value);
                            setDirty(true);
                          }}
                          disabled={detail.is_system || !canUpdate}
                          rows={2}
                        />
                      </div>
                      <div className="flex items-center gap-2 text-xs text-gilt-soft">
                        <code className="font-mono">{detail.key}</code>
                        {detail.is_system && (
                          <Badge variant="brand" size="sm">System role · read-only</Badge>
                        )}
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-2 shrink-0">
                      {canClone && (
                        <Button
                          variant="secondary"
                          size="sm"
                          leftIcon={<Copy />}
                          onClick={() => setCloneOpen(true)}
                        >
                          Clone
                        </Button>
                      )}
                      {canDelete && !detail.is_system && (
                        <Button
                          variant="danger-outline"
                          size="sm"
                          leftIcon={<Trash2 />}
                          onClick={() => setDeleteOpen(true)}
                        >
                          Delete
                        </Button>
                      )}
                    </div>
                  </div>
                </Card>

                {/* Permission matrix */}
                <Card>
                  <div className="px-5 py-3 border-b border-gilt-hairline flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gilt-ink">Permissions</h3>
                    <div className="flex items-center gap-2">
                      {savedAt && !dirty && (
                        <span className="font-mono text-xs text-gilt-credit tabular-nums">
                          Saved {savedAt.toLocaleTimeString()}
                        </span>
                      )}
                      {canUpdate && !detail.is_system && (
                        <Button
                          size="sm"
                          leftIcon={<Save />}
                          onClick={onSave}
                          loading={saving}
                          disabled={!dirty}
                        >
                          Save changes
                        </Button>
                      )}
                    </div>
                  </div>
                  <div className="p-2">
                    {permsByModule.map(([module, perms]) => {
                      const checkedInModule = perms.filter((p) => editPermKeys.has(p.key));
                      const allOn = checkedInModule.length === perms.length;
                      const some = checkedInModule.length > 0 && !allOn;
                      return (
                        <div key={module} className="p-3">
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <input
                                type="checkbox"
                                checked={allOn}
                                ref={(el) => {
                                  if (el) el.indeterminate = some;
                                }}
                                onChange={() => toggleModule(perms, allOn)}
                                disabled={detail.is_system || !canUpdate}
                                className="h-4 w-4 rounded border-gilt-hairline text-gilt-brass focus:ring-gilt-brass"
                              />
                              <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-gilt-soft">
                                {module}
                              </span>
                              <Badge variant="neutral" size="sm">
                                {checkedInModule.length}/{perms.length}
                              </Badge>
                            </div>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-1.5">
                            {perms.map((p) => {
                              const checked = editPermKeys.has(p.key);
                              return (
                                <label
                                  key={p.permission_id}
                                  className={cn(
                                    "flex items-start gap-2 p-2 rounded-md border transition-colors cursor-pointer",
                                    checked
                                      ? "border-gilt-brass/40 bg-gilt-sunken"
                                      : "border-gilt-hairline hover:border-gilt-brass hover:bg-gilt-sunken",
                                    (detail.is_system || !canUpdate) && "cursor-not-allowed opacity-70"
                                  )}
                                >
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => togglePerm(p.key)}
                                    disabled={detail.is_system || !canUpdate}
                                    className="mt-0.5 h-3.5 w-3.5 rounded border-gilt-hairline text-gilt-brass focus:ring-gilt-brass"
                                  />
                                  <div className="min-w-0">
                                    <code className="font-mono text-xs text-gilt-ink truncate block">
                                      {p.key}
                                    </code>
                                    {p.description && (
                                      <p className="text-2xs text-gilt-soft truncate">
                                        {p.description}
                                      </p>
                                    )}
                                  </div>
                                </label>
                              );
                            })}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </Card>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Create dialog */}
      <CreateRoleDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={async (newRole) => {
          setCreateOpen(false);
          await loadRoles();
          setSelectedId(newRole.role_id);
        }}
      />

      {/* Clone dialog */}
      <CloneRoleDialog
        open={cloneOpen}
        sourceRole={detail}
        onClose={() => setCloneOpen(false)}
        onCloned={async (newRole) => {
          setCloneOpen(false);
          await loadRoles();
          setSelectedId(newRole.role_id);
        }}
      />

      {/* Delete confirm */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Delete role?</DialogTitle>
            <DialogDescription>
              {detail
                ? `${detail.name} will be removed. Users currently assigned to this role will lose its permissions immediately.`
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={onDelete} leftIcon={<Trash2 />}>
              Delete role
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function CreateRoleDialog({ open, onClose, onCreated }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setErr("");
      setBusy(false);
    }
  }, [open]);
  const submit = async (e) => {
    e?.preventDefault();
    if (!name.trim()) return setErr("Name is required");
    setBusy(true);
    setErr("");
    try {
      const role = await rbacApi.createRole({ name, description });
      onCreated?.(role);
    } catch (e2) {
      setErr(e2?.response?.data?.detail?.message || e2.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose?.()}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Create role</DialogTitle>
          <DialogDescription>You can assign permissions after the role is created.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="px-6 py-3 space-y-3">
          <div>
            <Label required>Name</Label>
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Senior Accountant"
            />
          </div>
          <div>
            <Label>Description</Label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="What does this role do?"
            />
          </div>
          {err && <p className="text-xs text-gilt-debit">{err}</p>}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Create role
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CloneRoleDialog({ open, sourceRole, onClose, onCloned }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (open) {
      setName(sourceRole ? `${sourceRole.name} (copy)` : "");
      setErr("");
      setBusy(false);
    }
  }, [open, sourceRole]);
  const submit = async (e) => {
    e?.preventDefault();
    if (!sourceRole) return;
    if (!name.trim()) return setErr("Name is required");
    setBusy(true);
    setErr("");
    try {
      const role = await rbacApi.cloneRole(sourceRole.role_id, { new_name: name });
      onCloned?.(role);
    } catch (e2) {
      setErr(e2?.response?.data?.detail?.message || e2.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose?.()}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Clone role</DialogTitle>
          <DialogDescription>
            Creates a new role with the same permission set as {sourceRole?.name}.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="px-6 py-3 space-y-3">
          <div>
            <Label required>New role name</Label>
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          {err && <p className="text-xs text-gilt-debit">{err}</p>}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Clone role
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
