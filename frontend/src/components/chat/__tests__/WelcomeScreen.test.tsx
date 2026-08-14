import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "../../../i18n";
import { WelcomeScreen } from "../WelcomeScreen";

describe("WelcomeScreen", () => {
  const onExample = vi.fn();

  beforeAll(async () => {
    await i18n.changeLanguage("zh-CN");
  });

  beforeEach(() => onExample.mockClear());

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it.each([
    [5, "早上好。"],
    [12, "下午好。"],
    [17, "晚上好。"],
    [22, "夜深了，还在琢磨？"],
  ])("renders the local-hour greeting for %i:00", (hour, greeting) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 29, hour));
    vi.spyOn(Math, "random").mockReturnValue(0);

    render(<WelcomeScreen onExample={onExample} />);

    expect(screen.getByRole("heading", { name: greeting })).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t("welcome.taskSubtitle")),
    ).toBeInTheDocument();
  });

  it("hands off all four quick-action prompts unchanged", async () => {
    const actions = [
      {
        label: i18n.t("welcome.examples.valuationCheck"),
        prompt: i18n.t("welcome.examples.valuationCheckPrompt"),
      },
      {
        label: i18n.t("welcome.examples.optionsGreeks"),
        prompt: i18n.t("welcome.examples.optionsGreeksPrompt"),
      },
      {
        label: i18n.t("welcome.examples.crossMarketPortfolio"),
        prompt: i18n.t("welcome.examples.crossMarketPortfolioPrompt"),
      },
      {
        label: i18n.t("welcome.examples.investmentCommittee"),
        prompt: i18n.t("welcome.examples.investmentCommitteePrompt"),
      },
    ];
    const user = userEvent.setup();
    render(<WelcomeScreen onExample={onExample} />);

    const quickActions = screen.getByRole("group", { name: i18n.t("welcome.quickActions") });
    expect(within(quickActions).getAllByRole("button")).toHaveLength(4);

    for (const [index, action] of actions.entries()) {
      await user.click(
        within(quickActions).getByRole("button", { name: action.label }),
      );
      expect(onExample).toHaveBeenNthCalledWith(index + 1, action.prompt);
    }
    expect(onExample).toHaveBeenCalledTimes(4);
  });

  it("reveals eight category tabs and switches example cards from the disclosure", async () => {
    const user = userEvent.setup();
    render(<WelcomeScreen onExample={onExample} />);

    const trigger = screen.getByRole("button", { name: i18n.t("welcome.browseAllExamples") });
    const library = document.getElementById("welcome-example-library");
    expect(library).not.toBeNull();
    expect(library).toHaveAttribute("aria-hidden", "true");
    expect(
      screen.queryByRole("button", { name: /A股 MACD 策略/ }),
    ).not.toBeInTheDocument();

    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(library).toHaveAttribute("aria-hidden", "false");
    // One category at a time: eight tab chips, only the active cards render.
    expect(within(library!).getAllByRole("tab")).toHaveLength(8);
    expect(within(library!).getAllByRole("button")).toHaveLength(3);
    expect(within(library!).getAllByRole("button")[0]).toHaveClass(
      "focus-visible:ring-2",
      "focus-visible:ring-primary/40",
    );
    for (const category of [
      i18n.t("welcome.categories.multiMarketBacktest"),
      i18n.t("welcome.categories.researchAnalysis"),
      i18n.t("welcome.categories.valueInvesting"),
      i18n.t("welcome.categories.swarmTeams"),
      i18n.t("welcome.categories.docWebResearch"),
      i18n.t("welcome.categories.tradeJournal"),
      i18n.t("welcome.categories.tradingConnectors"),
      i18n.t("welcome.categories.shadowAccount"),
    ]) {
      expect(within(library!).getByText(category)).toBeInTheDocument();
    }

    await user.click(within(library!).getByRole("tab", { name: new RegExp(i18n.t("welcome.categories.valueInvesting")) }));
    expect(within(library!).getByRole("tab", { name: new RegExp(i18n.t("welcome.categories.valueInvesting")) })).toHaveAttribute("aria-selected", "true");
    expect(within(library!).getAllByRole("button")).toHaveLength(4);
    expect(
      within(library!).getByRole("button", { name: new RegExp(i18n.t("welcome.examples.valuationCheck")) }),
    ).toBeInTheDocument();
  });

  it("closes the example library with Escape and restores focus to its trigger", async () => {
    const user = userEvent.setup();
    render(<WelcomeScreen onExample={onExample} />);

    const trigger = screen.getByRole("button", { name: i18n.t("welcome.browseAllExamples") });
    await user.click(trigger);
    const library = document.getElementById("welcome-example-library")!;
    const firstExample = within(library).getAllByRole("button")[0];
    firstExample.focus();

    await user.keyboard("{Escape}");

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(library).toHaveAttribute("aria-hidden", "true");
    expect(trigger).toHaveFocus();
  });

  it("does not render capability chips", () => {
    render(<WelcomeScreen onExample={onExample} />);

    expect(screen.queryByText("金融技能库")).not.toBeInTheDocument();
    expect(screen.queryByText("智能体团队能力")).not.toBeInTheDocument();
    expect(screen.queryByText("影子账户回测能力")).not.toBeInTheDocument();
  });
});
