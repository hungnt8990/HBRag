"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { LockKeyhole, Loader2, Workflow } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { getErrorMessage, login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      router.replace("/");
    } catch (loginError) {
      setError(getErrorMessage(loginError));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-950">
      <Card className="w-full max-w-md bg-white shadow-sm">
        <CardHeader>
          <div className="mb-3 inline-flex w-fit items-center gap-2 rounded-full bg-slate-900 px-3 py-1 text-xs font-medium text-cyan-100">
            <Workflow className="h-3.5 w-3.5" />
            HBRag
          </div>
          <CardTitle className="flex items-center gap-2">
            <LockKeyhole className="h-5 w-5 text-cyan-700" />
            Sign in
          </CardTitle>
          <CardDescription>
            Use your organization account to access document ingestion and chat.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Username
              </span>
              <Input
                autoComplete="username"
                className="mt-2 border-slate-200 bg-white"
                onChange={(event) => setUsername(event.target.value)}
                value={username}
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Password
              </span>
              <Input
                autoComplete="current-password"
                className="mt-2 border-slate-200 bg-white"
                onChange={(event) => setPassword(event.target.value)}
                type="password"
                value={password}
              />
            </label>
            {error ? (
              <div
                className="rounded-xl border border-rose-200/70 bg-rose-50 px-4 py-3 text-sm text-rose-700"
                role="alert"
              >
                {error}
              </div>
            ) : null}
            <Button
              className="w-full bg-[#0d3b4c] text-white hover:bg-[#114e63]"
              disabled={!username.trim() || !password || submitting}
              type="submit"
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Sign in
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
