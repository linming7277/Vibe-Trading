import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { ValueLeaderMethodology } from "../ValueLeaderMethodology";

describe("ValueLeaderMethodology", () => {
  it("explains the candidate process in plain language and links back to research", () => {
    render(<MemoryRouter><ValueLeaderMethodology /></MemoryRouter>);

    expect(screen.getByRole("heading", { name: "量化候选公司是怎样选出来的？" })).toBeInTheDocument();
    expect(screen.getByText("不是所有股票都会直接被放进排名。", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("如果某一项指标缺失，系统不会用 0 分冒充真实表现", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /返回行业候选/ })).toHaveAttribute("href", "/value/leaders");
    expect(screen.getByRole("link", { name: "查看低估龙头池" })).toHaveAttribute("href", "/value");
  });
});
