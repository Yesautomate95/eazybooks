import React from "react";
import { useNavigate } from "react-router-dom";
import { ShieldAlert, ArrowLeft } from "lucide-react";
import { Button } from "../../ui/button";
import { Card } from "../../ui/card";

export default function NotAuthorized() {
  const navigate = useNavigate();
  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <Card padded className="max-w-md w-full text-center">
        <div className="inline-flex items-center justify-center h-12 w-12 rounded-xl bg-gilt-sunken border border-gilt-hairline text-gilt-brass mb-4">
          <ShieldAlert size={22} strokeWidth={1.75} />
        </div>
        <h1 className="gl-display text-lg text-gilt-ink">
          You don't have permission to view this page
        </h1>
        <p className="mt-2 text-sm text-gilt-soft">
          Ask an administrator to grant you access, or head back to your dashboard.
        </p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <Button variant="secondary" leftIcon={<ArrowLeft />} onClick={() => navigate(-1)}>
            Go back
          </Button>
          <Button onClick={() => navigate("/dashboard")}>Dashboard</Button>
        </div>
      </Card>
    </div>
  );
}
