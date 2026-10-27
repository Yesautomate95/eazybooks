import React, { useEffect, useMemo, useState } from "react";
import {
  Users,
  Plus,
  Search,
  MoreVertical,
  KeyRound,
  ShieldCheck,
  CircleSlash,
  UserCheck,
  Trash2,
  Copy,
  Check,
} from "lucide-react";
import { rbacApi } from "../../../services/api";
import { PageHeader } from "../../ui/page-header";
import { Card } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Input, Label } from "../../ui/input";
import { Skeleton } from "../../ui/skeleton";
import { EmptyState } from "../../ui/empty-state";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../../ui/dialog";
import { useRbac, PermissionGuard } from "../../rbac";
import { cn } from "../../../lib/utils";

const PAGE_SIZE = 25;

export default function UsersPage() {
  const { hasPermission, me, refresh: refreshRbac } = useRbac();
  const canCreate = hasPermission("users.create");
  const canUpdate = hasPermission("users.update");
  const canDelete = hasPermission("users.delete");
  const canAssignRoles = hasPermission("users.assign_roles");
  const canReset = hasPermission("users.reset_password");

  const [users, setUsers] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [rolesIndex, setRolesIndex] = useState([]); // {role_id, key, name}

  const [selected, setSelected] = useState(null); // detail user
  const [openDetail, setOpenDetail] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const load = async (opts = {}) => {
    setLoading(true);
    setError("");
    try {
      const data = await rbacApi.listUsers({
        search: opts.search ?? search,
        limit: PAGE_SIZE,
        offset: opts.offset ?? offset,
      });
      setUsers(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load({ offset: 0 });
    setOffset(0);
    (async () => {
      try {
        const r = await rbacApi.listRoles();
        setRolesIndex(r.items || []);
      } catch (e) {
        // non-blocking — roles list is for the assign dialog
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSearch = () => {
    setOffset(0);
    load({ offset: 0 });
  };

  const onPage = (next) => {
    setOffset(next);
    load({ offset: next });
  };

  const onOpenUser = async (userId) => {
    try {
      const detail = await rbacApi.getUser(userId);
      setSelected(detail);
      setOpenDetail(true);
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    }
  };

  const onUserUpdated = async (updated) => {
    setSelected(updated);
    await load();
    // If the actor changed their own roles, refresh RBAC so the UI re-evaluates
    if (me?.user_id && updated?.user_id === me.user_id) {
      await refreshRbac();
    }
  };

  const onResetPassword = async (userId) => {
    try {
      await rbacApi.resetUserPassword(userId);
      setError("Password reset successfully. Share the new password through a secure channel.");
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    }
  };

  const onDeactivate = async (userId) => {
    try {
      await rbacApi.deactivateUser(userId);
      await load();
      if (selected?.user_id === userId) {
        const refreshed = await rbacApi.getUser(userId);
        setSelected(refreshed);
      }
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    }
  };

  const onReactivate = async (userId) => {
    try {
      const updated = await rbacApi.updateUser(userId, { is_active: true });
      if (selected?.user_id === userId) setSelected(updated);
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err.message);
    }
  };

  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = Math.min(offset + users.length, total);

  return (
    <div className="min-h-screen">
      <PageHeader
        icon={Users}
        title="Users"
        description="Manage team members, their roles, and their access."
        actions={
          <div className="flex items-center gap-2">
            <div className="w-64">
              <Input
                size="md"
                leftIcon={<Search />}
                placeholder="Search by name or email"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && onSearch()}
              />
            </div>
            {canCreate && (
              <Button leftIcon={<Plus />} onClick={() => setCreateOpen(true)}>
                Invite user
              </Button>
            )}
          </div>
        }
      />

      <div className="px-6 lg:px-8 py-6 space-y-4">
        {error && (
          <Card padded className="border-gilt-debit/40 bg-gilt-sunken">
            <p className="text-sm text-gilt-debit">{error}</p>
          </Card>
        )}

        <Card>
          <div className="px-5 py-3 border-b border-gilt-hairline flex items-center justify-between">
            <p className="font-mono text-xs text-gilt-soft tabular-nums">
              {loading
                ? "Loading…"
                : total === 0
                ? "No users"
                : `Showing ${showingFrom}–${showingTo} of ${total}`}
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={offset === 0 || loading}
                onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={offset + users.length >= total || loading}
                onClick={() => onPage(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>

          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gilt-hairline">
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
                    User
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
                    Roles
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft w-32">
                    Status
                  </th>
                  <th className="px-2 py-2.5 w-12" />
                </tr>
              </thead>
              <tbody>
                {loading && users.length === 0 ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <tr key={i} className="border-t border-gilt-hairline">
                      <td className="px-4 py-3" colSpan={4}>
                        <Skeleton className="h-4 w-1/2" />
                      </td>
                    </tr>
                  ))
                ) : users.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-12">
                      <EmptyState
                        icon={Users}
                        size="sm"
                        title="No users yet"
                        description={
                          search
                            ? `No users match "${search}".`
                            : canCreate
                            ? "Invite your first team member to get started."
                            : "There are no users in the system."
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  users.map((u) => (
                    <tr
                      key={u.user_id}
                      className="border-t border-gilt-hairline hover:bg-gilt-sunken transition-colors cursor-pointer"
                      onClick={() => onOpenUser(u.user_id)}
                    >
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2.5">
                          <div className="h-8 w-8 rounded-full bg-gilt-sunken text-gilt-brass flex items-center justify-center text-xs font-display border border-gilt-hairline">
                            {(u.username || u.email || "?").trim().charAt(0).toUpperCase()}
                          </div>
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-gilt-ink truncate">
                              {u.username || u.email.split("@")[0]}
                            </p>
                            <p className="text-2xs text-gilt-soft truncate">{u.email}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex flex-wrap items-center gap-1">
                          {u.roles?.length ? (
                            u.roles.map((r) => (
                              <Badge
                                key={r.role_id}
                                variant={r.key === "super_admin" ? "brand" : "neutral"}
                                size="sm"
                              >
                                {r.name}
                              </Badge>
                            ))
                          ) : (
                            <span className="text-2xs text-gilt-faint italic">No roles</span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        {u.is_active ? (
                          <Badge variant="success" size="sm" dot>Active</Badge>
                        ) : (
                          <Badge variant="neutral" size="sm" dot>Inactive</Badge>
                        )}
                      </td>
                      <td className="px-2 py-2.5 text-right" onClick={(e) => e.stopPropagation()}>
                        <RowActions
                          user={u}
                          canReset={canReset}
                          canDelete={canDelete}
                          canUpdate={canUpdate}
                          onResetPassword={() => onResetPassword(u.user_id)}
                          onDeactivate={() => onDeactivate(u.user_id)}
                          onReactivate={() => onReactivate(u.user_id)}
                        />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* Detail drawer (dialog for now) */}
      <UserDetailDialog
        open={openDetail}
        user={selected}
        rolesIndex={rolesIndex}
        canAssignRoles={canAssignRoles}
        canUpdate={canUpdate}
        canReset={canReset}
        canDelete={canDelete}
        currentUserId={me?.user_id}
        onClose={() => setOpenDetail(false)}
        onUpdated={onUserUpdated}
        onResetPassword={onResetPassword}
        onDeactivate={onDeactivate}
        onReactivate={onReactivate}
      />

      {/* Create */}
      <CreateUserDialog
        open={createOpen}
        rolesIndex={rolesIndex}
        onClose={() => setCreateOpen(false)}
        onCreated={async () => {
          setCreateOpen(false);
          await load();
        }}
      />
    </div>
  );
}

function RowActions({ user, canReset, canDelete, canUpdate, onResetPassword, onDeactivate, onReactivate }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const handler = () => setOpen(false);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [open]);
  if (!canReset && !canDelete && !canUpdate) return null;
  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="h-7 w-7 inline-flex items-center justify-center rounded-md text-gilt-faint hover:bg-gilt-sunken hover:text-gilt-ink transition-colors"
        aria-label="Actions"
      >
        <MoreVertical size={14} />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-44 bg-gilt-card rounded-lg shadow-sm border border-gilt-hairline py-1 z-10"
          onClick={(e) => e.stopPropagation()}
        >
          {canReset && (
            <button
              onClick={() => {
                setOpen(false);
                onResetPassword();
              }}
              className="w-full text-left px-3 py-1.5 text-sm text-gilt-soft hover:bg-gilt-sunken transition-colors flex items-center gap-2"
            >
              <KeyRound size={13} />
              Reset password
            </button>
          )}
          {canUpdate && !user.is_active && (
            <button
              onClick={() => {
                setOpen(false);
                onReactivate();
              }}
              className="w-full text-left px-3 py-1.5 text-sm text-gilt-credit hover:bg-gilt-sunken transition-colors flex items-center gap-2"
            >
              <UserCheck size={13} />
              Reactivate
            </button>
          )}
          {canDelete && user.is_active && (
            <button
              onClick={() => {
                setOpen(false);
                onDeactivate();
              }}
              className="w-full text-left px-3 py-1.5 text-sm text-gilt-debit hover:bg-gilt-sunken transition-colors flex items-center gap-2"
            >
              <CircleSlash size={13} />
              Deactivate
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function UserDetailDialog({
  open,
  user,
  rolesIndex,
  canAssignRoles,
  canUpdate,
  canReset,
  canDelete,
  currentUserId,
  onClose,
  onUpdated,
  onResetPassword,
  onDeactivate,
  onReactivate,
}) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [roleKeys, setRoleKeys] = useState(new Set());
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  // Direct permissions (Map<permission_key, "grant" | "deny">) — represents
  // explicit grants and denies that override the role-derived set. The dialog
  // hydrates this from /direct-permissions on open and replaces the whole
  // set on save (whole-set semantics matches the role assignment flow).
  const [directPerms, setDirectPerms] = useState(new Map());
  const [allPermissions, setAllPermissions] = useState([]); // for the add picker

  useEffect(() => {
    if (open && user) {
      setUsername(user.username || "");
      setEmail(user.email || "");
      setRoleKeys(new Set((user.roles || []).map((r) => r.key)));
      setErr("");
      // Load direct permissions + the registry in parallel. Failure isn't fatal —
      // the rest of the dialog still works.
      (async () => {
        try {
          const [{ items: direct }, { items: all }] = await Promise.all([
            rbacApi.listUserDirectPermissions(user.user_id),
            rbacApi.listPermissions(),
          ]);
          const map = new Map();
          (direct || []).forEach((p) => map.set(p.key, p.is_grant ? "grant" : "deny"));
          setDirectPerms(map);
          setAllPermissions(all || []);
        } catch {
          // best-effort
        }
      })();
    }
  }, [open, user]);

  if (!user) return null;

  const isSelf = currentUserId && currentUserId === user.user_id;

  const onSave = async () => {
    setSaving(true);
    setErr("");
    try {
      let updated = user;
      // Normalize before comparing — DB-null fields land in state as "",
      // and we don't want to PATCH "old null → new empty string" (which
      // an EmailStr field rejects). Treat empty/whitespace as "unchanged"
      // unless the user actually typed something.
      const usernameTrim = (username || "").trim();
      const emailTrim = (email || "").trim();
      const usernameChanged = usernameTrim !== (user.username || "").trim();
      const emailChanged = emailTrim !== (user.email || "").trim();
      // Don't send an empty email — there's no API contract to "clear" it,
      // and Pydantic's EmailStr would 422 the request.
      const willSendEmail = emailChanged && emailTrim !== "";
      const willSendUsername = usernameChanged;
      if (willSendUsername || willSendEmail) {
        updated = await rbacApi.updateUser(user.user_id, {
          username: willSendUsername ? usernameTrim : undefined,
          email: willSendEmail ? emailTrim : undefined,
        });
      }
      if (canAssignRoles) {
        updated = await rbacApi.setUserRoles(user.user_id, Array.from(roleKeys));
        // Save direct permissions in the same flow when assign-roles is granted.
        const entries = Array.from(directPerms.entries()).map(([k, mode]) => ({
          permission_key: k,
          is_grant: mode === "grant",
        }));
        await rbacApi.setUserDirectPermissions(user.user_id, entries);
        // Re-fetch detail so effective permissions reflect the new overrides
        updated = await rbacApi.getUser(user.user_id);
      }
      onUpdated?.(updated);
    } catch (e2) {
      const detail = e2?.response?.data?.detail;
      let msg;
      if (typeof detail === "string") {
        msg = detail;
      } else if (detail && typeof detail === "object") {
        // FastAPI 422 returns detail as an array of {loc, msg, ...}.
        // Surface the first one so the operator can fix it.
        if (Array.isArray(detail)) {
          const first = detail[0];
          const field = (first?.loc || []).filter((p) => p !== "body").join(".") || "request";
          msg = `${field}: ${first?.msg || "invalid"}`;
        } else {
          msg = detail.message || JSON.stringify(detail);
        }
      } else {
        msg = e2.message;
      }
      setErr(msg);
    } finally {
      setSaving(false);
    }
  };

  const setDirectMode = (permKey, mode) => {
    setDirectPerms((prev) => {
      const next = new Map(prev);
      if (mode === "inherit") next.delete(permKey);
      else next.set(permKey, mode);
      return next;
    });
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !saving && onClose?.()}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              {user.username || user.email}
              {user.is_super_admin && (
                <Badge variant="brand" size="sm">Super admin</Badge>
              )}
              {!user.is_active && <Badge variant="neutral" size="sm" dot>Inactive</Badge>}
            </span>
          </DialogTitle>
          <DialogDescription>
            <span className="font-mono text-xs">{user.user_id}</span>
          </DialogDescription>
        </DialogHeader>

        <div className="px-6 py-3 space-y-5">
          {/* Profile */}
          <section>
            <h4 className="font-mono text-[10px] tracking-[0.14em] uppercase text-gilt-soft mb-2">
              Profile
            </h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <Label>Username</Label>
                <Input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={!canUpdate}
                />
              </div>
              <div>
                <Label>Email</Label>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={!canUpdate}
                />
              </div>
            </div>
          </section>

          {/* Roles */}
          <section>
            <h4 className="font-mono text-[10px] tracking-[0.14em] uppercase text-gilt-soft mb-2">
              Roles
            </h4>
            {rolesIndex.length === 0 ? (
              <p className="text-xs text-gilt-soft">No roles defined yet.</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                {rolesIndex.map((r) => {
                  const checked = roleKeys.has(r.key);
                  return (
                    <label
                      key={r.role_id}
                      className={cn(
                        "flex items-start gap-2 p-2 rounded-md border transition-colors cursor-pointer",
                        checked
                          ? "border-gilt-brass/40 bg-gilt-sunken"
                          : "border-gilt-hairline hover:border-gilt-brass hover:bg-gilt-sunken",
                        !canAssignRoles && "cursor-not-allowed opacity-70"
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          setRoleKeys((prev) => {
                            const next = new Set(prev);
                            next.has(r.key) ? next.delete(r.key) : next.add(r.key);
                            return next;
                          })
                        }
                        disabled={!canAssignRoles}
                        className="mt-0.5 h-3.5 w-3.5 rounded border-gilt-hairline text-gilt-brass focus:ring-gilt-brass"
                      />
                      <span className="text-sm text-gilt-ink">
                        {r.name}
                        {r.key === "super_admin" && (
                          <Badge variant="brand" size="sm" className="ml-1.5">System</Badge>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
            {isSelf && (
              <p className="mt-2 text-2xs text-gilt-brass">
                You're editing your own roles. The platform won't let you remove the last super-admin role.
              </p>
            )}
          </section>

          {/* Direct permissions — overrides on top of role-derived perms */}
          {!user.is_super_admin && (
            <section>
              <div className="flex items-center justify-between mb-2">
                <h4 className="font-mono text-[10px] tracking-[0.14em] uppercase text-gilt-soft">
                  Direct permissions
                </h4>
                <p className="text-2xs text-gilt-soft">
                  Overrides role-derived permissions · denies always win
                </p>
              </div>

              {directPerms.size === 0 ? (
                <p className="text-xs text-gilt-soft mb-2">
                  No direct overrides. The user's permissions come entirely from their roles.
                </p>
              ) : (
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {Array.from(directPerms.entries()).map(([key, mode]) => (
                    <span
                      key={key}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-2xs font-mono",
                        mode === "grant"
                          ? "border-gilt-credit/40 bg-gilt-sunken text-gilt-credit"
                          : "border-gilt-debit/40 bg-gilt-sunken text-gilt-debit"
                      )}
                    >
                      <span className="font-semibold text-2xs">
                        {mode === "grant" ? "+" : "−"}
                      </span>
                      {key}
                      {canAssignRoles && (
                        <button
                          type="button"
                          onClick={() => setDirectMode(key, "inherit")}
                          className="ml-0.5 inline-flex h-3.5 w-3.5 items-center justify-center rounded hover:bg-gilt-sunken transition-colors"
                          aria-label={`Remove override for ${key}`}
                        >
                          ×
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              )}

              {canAssignRoles && (
                <DirectPermissionPicker
                  allPermissions={allPermissions}
                  currentDirect={directPerms}
                  onAdd={(key, mode) => setDirectMode(key, mode)}
                />
              )}
            </section>
          )}

          {/* Effective permissions */}
          <section>
            <h4 className="font-mono text-[10px] tracking-[0.14em] uppercase text-gilt-soft mb-2">
              Effective permissions
            </h4>
            {user.is_super_admin ? (
              <p className="text-sm text-gilt-soft">
                <Badge variant="brand" size="sm">Super admin</Badge>
                <span className="ml-2">Holds every permission in the system.</span>
              </p>
            ) : user.permissions?.length ? (
              <div className="flex flex-wrap gap-1.5 max-h-40 overflow-auto p-2 bg-gilt-sunken border border-gilt-hairline rounded-md">
                {user.permissions.map((p) => (
                  <code
                    key={p}
                    className="font-mono text-2xs bg-gilt-card border border-gilt-hairline rounded px-1.5 py-0.5 text-gilt-soft"
                  >
                    {p}
                  </code>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gilt-soft">No permissions yet.</p>
            )}
            <p className="mt-1.5 text-2xs text-gilt-soft">
              Effective = (∪ role permissions) ∪ direct grants − direct denies
            </p>
          </section>

          {err && (
            <div className="text-xs text-gilt-debit bg-gilt-sunken border border-gilt-debit/40 rounded p-2">
              {err}
            </div>
          )}
        </div>

        <DialogFooter>
          <div className="flex-1 flex items-center gap-2">
            {canReset && (
              <Button
                variant="tertiary"
                size="sm"
                leftIcon={<KeyRound />}
                onClick={() => onResetPassword(user.user_id)}
              >
                Reset password
              </Button>
            )}
            {canDelete && user.is_active && !isSelf && (
              <Button
                variant="danger-outline"
                size="sm"
                leftIcon={<CircleSlash />}
                onClick={() => onDeactivate(user.user_id)}
              >
                Deactivate
              </Button>
            )}
            {canUpdate && !user.is_active && (
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<UserCheck />}
                onClick={() => onReactivate(user.user_id)}
              >
                Reactivate
              </Button>
            )}
          </div>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={onSave} loading={saving}>
            Save changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CreateUserDialog({ open, rolesIndex, onClose, onCreated }) {
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [roleKeys, setRoleKeys] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (open) {
      setEmail("");
      setUsername("");
      setPassword("");
      setRoleKeys(new Set());
      setErr("");
      setBusy(false);
    }
  }, [open]);
  const submit = async (e) => {
    e?.preventDefault();
    if (!email.trim()) return setErr("Email is required");
    if (password.length < 12) return setErr("Password must be at least 12 characters");
    setBusy(true);
    setErr("");
    try {
      const res = await rbacApi.createUser({
        email,
        username,
        password,
        role_keys: Array.from(roleKeys),
      });
      onCreated?.(res);
    } catch (e2) {
      setErr(e2?.response?.data?.detail?.message || e2.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose?.()}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Invite user</DialogTitle>
          <DialogDescription>
            Set a strong initial password and share it through a secure channel.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="px-6 py-3 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <Label required>Email</Label>
              <Input
                autoFocus
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="finance@example.com"
              />
            </div>
            <div>
              <Label>Display name</Label>
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Optional"
              />
            </div>
          </div>
          <div>
            <Label required>Initial password</Label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 12 characters"
            />
          </div>
          <div>
            <Label>Roles</Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-48 overflow-auto p-2 border border-gilt-hairline rounded-md">
              {rolesIndex.map((r) => {
                const checked = roleKeys.has(r.key);
                return (
                  <label key={r.role_id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setRoleKeys((prev) => {
                          const next = new Set(prev);
                          next.has(r.key) ? next.delete(r.key) : next.add(r.key);
                          return next;
                        })
                      }
                      className="h-3.5 w-3.5 rounded border-gilt-hairline text-gilt-brass focus:ring-gilt-brass"
                    />
                    <span>{r.name}</span>
                  </label>
                );
              })}
            </div>
          </div>
          {err && <p className="text-xs text-gilt-debit">{err}</p>}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!email.trim()}>
            Create user
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CopyableSecret({ value }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <code className="flex-1 font-mono text-sm bg-gilt-sunken border border-gilt-hairline rounded-md px-3 py-2 text-gilt-ink break-all">
        {value}
      </code>
      <Button
        variant="secondary"
        size="md"
        leftIcon={copied ? <Check /> : <Copy />}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            // older browsers — ignore
          }
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

/**
 * DirectPermissionPicker — small inline control that picks a permission
 * from the catalogue and adds it as an explicit grant or deny. We exclude
 * permissions that already have an override so the UI doesn't let admins
 * "double-apply".
 */
function DirectPermissionPicker({ allPermissions, currentDirect, onAdd }) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(""); // permission key

  const available = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (allPermissions || []).filter((p) => {
      if (currentDirect.has(p.key)) return false;
      if (!q) return true;
      return (
        p.key.toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q) ||
        (p.module || "").toLowerCase().includes(q)
      );
    });
  }, [allPermissions, currentDirect, query]);

  return (
    <div className="bg-gilt-sunken border border-gilt-hairline rounded-md p-2 space-y-2">
      <div className="flex items-center gap-2">
        <div className="flex-1">
          <Input
            size="sm"
            placeholder="Search permission to override…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <div className="max-h-32 overflow-auto">
        {available.length === 0 ? (
          <p className="text-2xs text-gilt-soft py-2 px-1">
            {query ? "No matches." : "All permissions already overridden."}
          </p>
        ) : (
          available.slice(0, 20).map((p) => (
            <div
              key={p.permission_id}
              className="flex items-center gap-2 py-1 px-1 hover:bg-gilt-card rounded transition-colors"
            >
              <code className="flex-1 font-mono text-2xs text-gilt-soft truncate">
                {p.key}
              </code>
              <button
                type="button"
                onClick={() => onAdd(p.key, "grant")}
                className="inline-flex items-center gap-0.5 text-2xs font-medium text-gilt-credit hover:bg-gilt-card transition-colors rounded px-1.5 py-0.5 border border-gilt-credit/40"
                title="Grant this permission directly (even if role doesn't)"
              >
                <span className="font-bold">+</span> Grant
              </button>
              <button
                type="button"
                onClick={() => onAdd(p.key, "deny")}
                className="inline-flex items-center gap-0.5 text-2xs font-medium text-gilt-debit hover:bg-gilt-card transition-colors rounded px-1.5 py-0.5 border border-gilt-debit/40"
                title="Deny this permission (overrides any role grant)"
              >
                <span className="font-bold">−</span> Deny
              </button>
            </div>
          ))
        )}
        {available.length > 20 && (
          <p className="text-2xs text-gilt-soft px-1 pt-1">
            Showing first 20 — refine the search to see more.
          </p>
        )}
      </div>
    </div>
  );
}
