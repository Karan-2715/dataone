"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface NotificationItem {
  id: number; event_key: string; title: string; body: string;
  link: string | null; created_at: string; read: boolean;
}

export default function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [open]);

  const refresh = useCallback(async () => {
    try {
      const [feed, count] = await Promise.all([
        api.get<{ notifications: NotificationItem[] }>("/api/v1/notifications?limit=30"),
        api.get<{ unread: number }>("/api/v1/notifications/unread-count"),
      ]);
      setItems(feed.notifications);
      setUnread(count.unread);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Notifications unavailable.");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const markRead = async (item: NotificationItem) => {
    if (!item.read) {
      await api.patch(`/api/v1/notifications/${item.id}/read`);
      setItems((current) => current.map((entry) => entry.id === item.id ? { ...entry, read: true } : entry));
      setUnread((current) => Math.max(0, current - 1));
    }
  };

  return (
    <div className="relative" ref={containerRef}>
      <button type="button" onClick={() => { setOpen((value) => !value); void refresh(); }} aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`} aria-expanded={open} aria-haspopup="dialog" aria-controls="notification-center-panel" className="relative flex h-9 w-9 items-center justify-center rounded-xl text-fg-muted hover:bg-surface-overlay">
        <span aria-hidden="true">🔔</span>
        {unread > 0 && <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-red-500 px-1 text-[9px] font-bold text-white">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <section id="notification-center-panel" aria-label="Notification center" className="glass-strong absolute right-0 top-11 z-40 w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-border-strong shadow-2xl">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-fg">Notifications</h2>
            <span className="text-xs text-fg-subtle">{unread} unread</span>
          </div>
          <div className="max-h-96 overflow-y-auto p-2">
            {error ? <p role="alert" className="p-3 text-xs text-red-500">{error}</p>
              : items.length === 0 ? <p className="p-6 text-center text-sm text-fg-subtle">No notifications yet.</p>
              : items.map((item) => {
                const content = (
                  <>
                    <span className="block text-sm font-medium text-fg">{item.title}</span>
                    {item.body && <span className="mt-1 block text-xs text-fg-muted">{item.body}</span>}
                    <span className="mt-1 block text-[10px] text-fg-subtle">{new Date(item.created_at).toLocaleString()}</span>
                  </>
                );
                const className = `block rounded-xl border px-3 py-2.5 ${item.read ? "border-transparent" : "border-accent/30 bg-accent-soft"} hover:bg-surface-overlay`;
                return item.link
                  ? <Link key={item.id} href={item.link} onClick={() => void markRead(item)} className={className}>{content}</Link>
                  : <button type="button" key={item.id} onClick={() => void markRead(item)} className={`${className} w-full text-left`}>{content}</button>;
              })}
          </div>
        </section>
      )}
    </div>
  );
}
