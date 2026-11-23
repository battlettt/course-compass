# Hypothesis Testing Basics

## Why test hypotheses

A hypothesis test is a structured way to decide whether data give enough evidence against a default claim. We never prove a claim true; we only ask whether the observed data would be surprising if the default claim were correct.

## Key terms

- **Null hypothesis**: the default claim of no effect or no difference, written H0, which the test tries to find evidence against.
- **Alternative hypothesis**: the competing claim, written H1, that there is an effect or a difference.
- **P-value**: the probability of observing data at least as extreme as the sample, assuming the null hypothesis is true.
- **Significance level**: the threshold alpha, often 0.05, chosen before the test, below which a p-value leads us to reject the null hypothesis.
- **Test statistic**: a single number computed from the sample, such as a t or z score, that measures how far the data sit from what the null hypothesis predicts.

## Errors in testing

- **Type I error**: rejecting a null hypothesis that is actually true; its probability equals the significance level.
- **Type II error**: failing to reject a null hypothesis that is actually false.
- **Statistical power**: the probability that a test correctly rejects a false null hypothesis, equal to one minus the Type II error rate.

Power grows with larger samples, larger true effects, and a higher significance level. A small study can miss a real effect simply because it had low power.

## Reading a result

If the p-value is 0.03 and alpha is 0.05, we reject the null hypothesis. This does not mean there is a 3 percent chance the null is true. It means data this extreme would occur about 3 percent of the time if the null were true. A confidence interval that excludes the null value tells the same story as a two-sided test at the matching level.
