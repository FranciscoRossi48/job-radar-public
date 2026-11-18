import { CheckCircle2, CircleAlert, Info, X } from "lucide-react";
import { createContext, use, useCallback, useMemo, useState } from "react";

type ToastTone = "success" | "error" | "info";
interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}
interface ToastContextValue {
  notify: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);
let nextId = 1;

const icons = {
  success: CheckCircle2,
  error: CircleAlert,
  info: Info,
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const remove = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);
  const notify = useCallback(
    (message: string, tone: ToastTone = "info") => {
      const id = nextId++;
      setItems((current) => [...current, { id, message, tone }]);
      window.setTimeout(() => remove(id), 4_500);
    },
    [remove],
  );
  const value = useMemo(() => ({ notify }), [notify]);
  return (
    <ToastContext value={value}>
      {children}
      <div className="toast-region" role="region" aria-label="Notifications">
        {items.map((item) => {
          const Icon = icons[item.tone];
          return (
            <div className={`toast toast--${item.tone}`} role="status" key={item.id}>
              <Icon size={18} aria-hidden="true" />
              <span>{item.message}</span>
              <button className="icon-button icon-button--small" onClick={() => remove(item.id)} aria-label="Dismiss notification">
                <X size={16} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext>
  );
}

export function useToast() {
  const context = use(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
