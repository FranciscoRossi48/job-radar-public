import { CircleAlert } from "lucide-react";
import { useEffect, useRef } from "react";
import { Button } from "./ui";

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby="confirm-title" onCancel={(event) => { event.preventDefault(); onClose(); }} onClose={onClose}>
      <div className="confirm-dialog__icon"><CircleAlert size={22} aria-hidden="true" /></div>
      <h2 id="confirm-title">{title}</h2>
      <p>{description}</p>
      <div className="confirm-dialog__actions">
        <Button variant="ghost" autoFocus onClick={onClose}>Cancel</Button>
        <Button onClick={() => { onConfirm(); onClose(); }}>{confirmLabel}</Button>
      </div>
    </dialog>
  );
}
