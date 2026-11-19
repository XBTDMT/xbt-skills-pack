# How the shop runs shelfkit

The shop has two tills, one at the front counter and one at the trade desk. Both run shelfkit against the same
inventory file, which lives on the shop's shared drive. A sale is recorded the moment it is rung up, so on a busy
Saturday the two tills record sales within the same second.

The back office opens the same file once a day to print the low-stock report.
