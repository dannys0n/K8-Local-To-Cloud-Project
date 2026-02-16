"use client";

import { ChevronDown, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MOCK_USERS } from "@/lib/clerk-mock/users";
import { useUser } from "@clerk/nextjs";

const COOKIE_NAME = "mock_user_id";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365; // 1 year

function setMockUserCookie(userId: string) {
  document.cookie = `${COOKIE_NAME}=${userId}; path=/; max-age=${COOKIE_MAX_AGE}`;
  window.location.reload();
}

export function MockUserSwitcher() {
  const { user } = useUser();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="gap-1.5 text-xs text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          <Users className="h-3.5 w-3.5" />
          {user.firstName} {user.lastName}
          <ChevronDown className="h-3 w-3" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-48">
        <DropdownMenuLabel className="text-xs font-normal text-zinc-500">
          Switch mock user
        </DropdownMenuLabel>
        {MOCK_USERS.map((u) => (
          <DropdownMenuItem
            key={u.id}
            onClick={() => setMockUserCookie(u.id)}
            className={user.id === u.id ? "bg-accent" : ""}
          >
            {u.firstName} {u.lastName}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
