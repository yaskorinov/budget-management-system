from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddContribution(StatesGroup):
    amount = State()


class AddPurchase(StatesGroup):
    text = State()


class EditOperation(StatesGroup):
    amount = State()
    title = State()


class NewGroup(StatesGroup):
    title = State()
