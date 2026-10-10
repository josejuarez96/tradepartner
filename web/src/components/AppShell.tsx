import type { CSSProperties, ReactNode } from "react";
import { BookOpen, FlaskConical, LayoutDashboard, TrendingUp } from "lucide-react";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarHeader, SidebarInset,
  SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { ThemeSwitch } from "./ThemeSwitch";

export type Screen = "overview" | "books" | "research";

const NAV: { key: Screen; label: string; icon: typeof LayoutDashboard }[] = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "books", label: "Books", icon: BookOpen },
  { key: "research", label: "Research", icon: FlaskConical },
];

interface Props {
  screen: Screen;
  title: string;
  mode: "paper" | "live";
  sample: boolean;
  /** Count of things waiting on the owner, per screen. */
  counts: Partial<Record<Screen, number>>;
  children: ReactNode;
}

/** shadcn dashboard-01 layout: inset sidebar, a slim header, content. The sidebar becomes a sheet on phone. */
export function AppShell({ screen, title, mode, sample, counts, children }: Props) {
  return (
    <SidebarProvider style={{ "--sidebar-width": "15rem", "--header-height": "3.25rem" } as CSSProperties}>
      <Sidebar variant="inset" collapsible="offcanvas">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild className="data-[slot=sidebar-menu-button]:p-1.5!">
                <a href="#overview">
                  <span className="grid size-6 place-items-center rounded-md bg-primary text-primary-foreground">
                    <TrendingUp className="size-3.5!" />
                  </span>
                  <span className="text-base font-semibold">TradePartner</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV.map((n) => (
                  <SidebarMenuItem key={n.key}>
                    <SidebarMenuButton asChild isActive={n.key === screen} tooltip={n.label}>
                      <a href={`#${n.key}`}>
                        <n.icon />
                        <span>{n.label}</span>
                      </a>
                    </SidebarMenuButton>
                    {!!counts[n.key] && (
                      <SidebarMenuBadge className="bg-attention-soft text-attention" aria-label={`${counts[n.key]} waiting on you`}>
                        {counts[n.key]}
                      </SidebarMenuBadge>
                    )}
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <div className="rounded-lg border p-3 text-sm">
            <p className="font-medium">{mode === "paper" ? "Paper account" : "Live account"}</p>
            <p className="text-muted-foreground text-xs">
              {mode === "paper" ? "Practice money on Alpaca. Nothing real is at risk." : "Real money."}
            </p>
          </div>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset>
        <header className="flex h-(--header-height) shrink-0 items-center gap-2 border-b">
          <div className="flex w-full items-center gap-1 px-4 lg:gap-2 lg:px-6">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mx-2 data-[orientation=vertical]:h-4" />
            <h1 className="text-base font-medium">{title}</h1>
            <div className="ml-auto flex items-center gap-2">
              {sample && (
                <Badge variant="outline" className="text-muted-foreground" title="Every number here is made up">
                  Sample data
                </Badge>
              )}
              <ThemeSwitch />
            </div>
          </div>
        </header>
        <div className="@container/main flex flex-1 flex-col">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  );
}
