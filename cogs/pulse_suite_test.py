from discord.ext import commands
class TestCog(commands.Cog):
    pass
async def setup(bot):
    await bot.add_cog(TestCog(bot))
