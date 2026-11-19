# Receipt board: native use and external replay

Register 2 native trials before launch. The same 2 seats run in both trials. The source world shows each seat its 2 options. The target world shows each seat the first option only. Do not replace failed or missing trials. Run on the stock Olam multi-agent runtime, with no model provider.

The policy chooses the last visible option. The coordinator does not run this policy while merging its worker receipts. Forecasts below are hypotheses, not results:

- Both teams receive the native task reward if both seats submit a permitted option. Removing an option need not change that reward.
- Each target action differs from its source action. Projecting each source view to its first option gives the input accepted by the corresponding target seat.
- A detached worker that runs the specified policy on each projected view returns the action recorded by that target seat. An identity projection does not forecast the change.

Compare view digests, actions and task rewards separately. A digest acknowledges view bytes; it does not prove which process generated a response. A matching external response does not attest execution. Do not turn team rewards into seat rewards. Do not infer population effects from 2 trials.
