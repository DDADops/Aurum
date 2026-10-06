import { useState } from "react";
import { Plus } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { AccountList } from "@/components/accounts/AccountList";
import { AccountFormModal } from "@/components/accounts/AccountFormModal";
import { useAccounts, useDeleteAccount, useUpdateAccount } from "@/hooks/useAccounts";
import { useTranslation } from "@/lib/i18n";
import type { AccountWithBalance } from "@/types";

export function AccountsPage() {
  const { t } = useTranslation();
  const [showArchived, setShowArchived] = useState(false);
  const { data: accounts, isLoading } = useAccounts(showArchived);
  const updateAccount = useUpdateAccount();
  const deleteAccount = useDeleteAccount();

  const [modalOpen, setModalOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<AccountWithBalance | null>(null);

  function openCreateModal() {
    setEditingAccount(null);
    setModalOpen(true);
  }

  function openEditModal(account: AccountWithBalance) {
    setEditingAccount(account);
    setModalOpen(true);
  }

  function handleToggleArchived(account: AccountWithBalance) {
    updateAccount.mutate({ id: account.id, input: { is_archived: !account.is_archived } });
  }

  function handleDelete(account: AccountWithBalance) {
    if (window.confirm(t("account.confirmDelete", { name: account.name }))) {
      deleteAccount.mutate(account.id);
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>{t("nav.accounts")}</CardTitle>
          <Button onClick={openCreateModal}>
            <Plus size={16} />
            {t("common.add")}
          </Button>
        </CardHeader>
        <CardContent>
          <label className="mb-3 flex items-center gap-2 text-xs text-text-muted">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(event) => setShowArchived(event.target.checked)}
              className="h-3.5 w-3.5 accent-text-primary"
            />
            {t("account.showArchived")}
          </label>
          {isLoading ? (
            <p className="py-10 text-center text-sm text-text-muted">{t("common.loading")}</p>
          ) : (
            <AccountList
              items={accounts ?? []}
              onEdit={openEditModal}
              onToggleArchived={handleToggleArchived}
              onDelete={handleDelete}
            />
          )}
        </CardContent>
      </Card>

      <AccountFormModal open={modalOpen} onClose={() => setModalOpen(false)} account={accounts?.find((item) => item.id === editingAccount?.id) ?? editingAccount} />
    </div>
  );
}
